"""
Liquidity Signal Pipeline — Phase 4

Orchestrates:
  Binance Order Book WebSocket → LiquidityEngine → AlertDispatcher → Telegram

Detects:
  - Buy/sell walls (single levels with anomalously large size)
  - Order book imbalance (directional bid/ask pressure)
"""
import asyncio
import os
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from alerts.dispatcher import AlertDispatcher
from config.settings import get_neon
from core.models import LiquiditySignal, OrderBook
from data.ingestion.orderbook_collector import BinanceOrderBookCollector
from data.storage.neon import get_repository, init_pool, run_migrations
from signals.liquidity_whale import LiquidityEngine
from utils.logging import get_logger

log = get_logger("pipeline.liquidity")


class LiquidityPipeline:
    """
    Phase 4: Real-time order book liquidity signal pipeline.

    Monitors top 20 bid/ask levels on Binance for 8 major pairs.
    Fires alerts when walls or significant imbalances are detected.
    """

    def __init__(self):
        self._engine = LiquidityEngine(on_signal=self._on_liquidity_signal)
        self._dispatcher = AlertDispatcher()
        self._running = False
        self._signals_generated = 0
        self._snapshots_processed = 0
        self._session: aiohttp.ClientSession | None = None
        self._tasks = []

    async def initialize(self) -> None:
        log.info("Initializing liquidity pipeline")
        await self._dispatcher.start()

        cfg = get_neon()
        if cfg.is_configured:
            await init_pool()
            await run_migrations()
            log.info("Neon DB connected")
        else:
            log.warning("DATABASE_URL not set — liquidity signals won't be persisted")

        log.info("Liquidity pipeline initialized")

    async def start(self) -> None:
        self._running = True
        self._session = aiohttp.ClientSession()

        collector = BinanceOrderBookCollector(
            on_orderbook=self._handle_orderbook,
            update_interval_ms=1000,
        )

        self._tasks = [
            asyncio.create_task(collector.start(self._session), name="binance-orderbook"),
            asyncio.create_task(self._status_reporter(), name="liquidity-status"),
        ]

        log.info("Liquidity pipeline running", symbols=len(collector.symbols))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._session:
            await self._session.close()
        await self._dispatcher.stop()
        log.info("Liquidity pipeline stopped", signals_generated=self._signals_generated)

    async def _handle_orderbook(self, ob: OrderBook) -> None:
        self._snapshots_processed += 1
        await self._engine.on_orderbook(ob)

    async def _on_liquidity_signal(self, signal: LiquiditySignal) -> None:
        self._signals_generated += 1

        log.info(
            "LIQUIDITY SIGNAL",
            symbol=signal.symbol,
            exchange=signal.exchange,
            wall_side=signal.wall_side.value,
            wall_price=signal.wall_price,
            wall_size_usd=round(signal.wall_size_usd, 0),
            imbalance=round(signal.imbalance_ratio, 3),
            direction=signal.direction.value,
            strength=signal.strength.value,
            confidence=signal.confidence,
        )

        cfg = get_neon()
        if cfg.is_configured:
            try:
                repo = get_repository()
                await repo.save_liquidity(signal)
            except Exception as e:
                log.error("Failed to persist liquidity signal", error=str(e))

        await self._dispatcher.dispatch_signal(signal)

    async def _status_reporter(self) -> None:
        while self._running:
            await asyncio.sleep(300)
            log.info(
                "Liquidity pipeline health",
                snapshots_processed=self._snapshots_processed,
                signals_generated=self._signals_generated,
                symbols_tracked=len(self._engine._ob_stats),
            )
