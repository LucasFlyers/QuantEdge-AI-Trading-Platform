"""
Whale Signal Pipeline — Phase 3

Orchestrates:
  Etherscan (ETH + ERC-20) + Bitcoin blockchain.info
  → WhaleEngine → AlertDispatcher → Telegram

Large transfers classified as exchange deposits (bearish) or
withdrawals (bullish) and fired as Telegram alerts.
"""
import asyncio
import os
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from alerts.dispatcher import AlertDispatcher
from config.settings import get_neon
from core.models import WhaleSignal
from data.ingestion.whale_collectors import (
    BitcoinWhaleCollector,
    EtherscanCollector,
    RawTransaction,
)
from data.storage.neon import get_repository, init_pool, run_migrations
from signals.liquidity_whale import WhaleTx, WhaleEngine
from utils.logging import get_logger

log = get_logger("pipeline.whale")


def _to_whale_tx(raw: RawTransaction) -> WhaleTx:
    """Convert raw collector tx to engine's WhaleTx dataclass."""
    return WhaleTx(
        chain=raw.chain,
        tx_hash=raw.tx_hash,
        from_address=raw.from_address,
        to_address=raw.to_address,
        asset=raw.asset,
        amount=raw.amount,
        amount_usd=raw.amount_usd,
        block_number=raw.block_number,
        timestamp=raw.timestamp,
    )


class WhalePipeline:
    """
    Phase 3: On-chain whale activity signal pipeline.

    Monitors:
      - Ethereum: ETH + USDT, USDC, WBTC, DAI transfers ≥ $1M
      - Bitcoin: BTC transfers ≥ 100 BTC
    """

    def __init__(self):
        self._engine = WhaleEngine(on_signal=self._on_whale_signal)
        self._dispatcher = AlertDispatcher()
        self._running = False
        self._signals_generated = 0
        self._tx_processed = 0
        self._session: aiohttp.ClientSession | None = None
        self._tasks = []

    async def initialize(self) -> None:
        log.info("Initializing whale pipeline")
        await self._dispatcher.start()

        cfg = get_neon()
        if cfg.is_configured:
            await init_pool()
            await run_migrations()
            log.info("Neon DB connected")
        else:
            log.warning("DATABASE_URL not set — whale signals won't be persisted")

        log.info("Whale pipeline initialized")

    async def start(self) -> None:
        self._running = True
        self._session = aiohttp.ClientSession()

        min_usd = float(os.getenv("WHALE_MIN_USD", "1000000"))
        min_btc = float(os.getenv("WHALE_MIN_BTC", "100"))

        eth_collector = EtherscanCollector(
            interval_s=30,
            on_transaction=self._handle_tx,
            min_usd=min_usd,
        )
        btc_collector = BitcoinWhaleCollector(
            interval_s=60,
            on_transaction=self._handle_tx,
            min_btc=min_btc,
        )

        self._tasks = [
            asyncio.create_task(eth_collector.start(self._session), name="etherscan"),
            asyncio.create_task(btc_collector.start(self._session), name="bitcoin"),
            asyncio.create_task(self._status_reporter(), name="whale-status"),
        ]

        log.info(
            "Whale pipeline running",
            min_usd=min_usd,
            min_btc=min_btc,
            etherscan_key=bool(os.getenv("ETHERSCAN_API_KEY")),
        )

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._session:
            await self._session.close()
        await self._dispatcher.stop()
        log.info("Whale pipeline stopped", signals_generated=self._signals_generated)

    async def _handle_tx(self, raw: RawTransaction) -> None:
        self._tx_processed += 1
        whale_tx = _to_whale_tx(raw)
        await self._engine.on_transaction(whale_tx)

    async def _on_whale_signal(self, signal: WhaleSignal) -> None:
        self._signals_generated += 1

        log.info(
            "WHALE SIGNAL",
            asset=signal.asset,
            amount_usd=round(signal.amount_usd, 0),
            move_type=signal.move_type.value,
            exchange=signal.exchange_name,
            direction=signal.direction.value,
            strength=signal.strength.value,
            confidence=signal.confidence,
            tx_hash=signal.tx_hash[:16],
        )

        cfg = get_neon()
        if cfg.is_configured:
            try:
                repo = get_repository()
                await repo.save_whale(signal)
            except Exception as e:
                log.error("Failed to persist whale signal", error=str(e))

        await self._dispatcher.dispatch_signal(signal)

    async def _status_reporter(self) -> None:
        while self._running:
            await asyncio.sleep(300)
            log.info(
                "Whale pipeline health",
                tx_processed=self._tx_processed,
                signals_generated=self._signals_generated,
            )
