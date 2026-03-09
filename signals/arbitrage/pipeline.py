"""
Arbitrage Pipeline — Phase 1 MVP Orchestrator

Wires together:
  ExchangeConnectors → ArbitrageEngine → AlertDispatcher

This is the first production-ready signal pipeline.
Run directly for MVP mode; imported by full platform for phase 2+.
"""
import asyncio
import signal as os_signal
import sys
import os
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import get_arbitrage, get_exchanges, get_platform
from core.models import ArbitrageSignal, PriceTick
from data.connectors.exchange_connectors import build_connector, ExchangeConnector
from signals.arbitrage.engine import ArbitrageEngine
from alerts.dispatcher import AlertDispatcher
from utils.logging import configure_logging, get_logger

log = get_logger("pipeline.arbitrage")


class ArbitragePipeline:
    """
    Phase 1 MVP: End-to-end arbitrage signal pipeline.

    Architecture:
      [Binance WS] ─┐
      [Coinbase WS] ─┤─→ [ArbitrageEngine] ─→ [AlertDispatcher]
      [Kraken WS]  ─┘         │
                         [Signal Log]
    """

    def __init__(self, exchanges: Optional[List[str]] = None):
        self.config = get_arbitrage()
        self.platform_cfg = get_platform()

        self.exchanges = exchanges or ["binance", "coinbase", "kraken"]
        self._connectors: Dict[str, ExchangeConnector] = {}
        self._engine: Optional[ArbitrageEngine] = None
        self._dispatcher: Optional[AlertDispatcher] = None
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._signals_generated = 0

    async def initialize(self) -> None:
        """Initialize all pipeline components."""
        log.info(
            "Initializing arbitrage pipeline",
            exchanges=self.exchanges,
            symbols=self.config.symbols,
        )

        # Initialize alert dispatcher
        self._dispatcher = AlertDispatcher()
        await self._dispatcher.start()

        # Initialize arbitrage engine
        self._engine = ArbitrageEngine(
            config=self.config,
            on_signal=self._on_arbitrage_signal,
        )

        # Build exchange connectors
        for exchange in self.exchanges:
            try:
                connector = build_connector(
                    exchange=exchange,
                    symbols=self.config.symbols,
                    on_tick=self._engine.on_tick,
                )
                self._connectors[exchange] = connector
                log.info("Connector built", exchange=exchange)
            except ValueError as e:
                log.warning("Skipping connector", exchange=exchange, reason=str(e))

        log.info(
            "Pipeline initialized",
            active_exchanges=list(self._connectors.keys()),
        )

    async def start(self) -> None:
        """Start all connectors and begin processing."""
        if not self._connectors:
            raise RuntimeError("Pipeline not initialized. Call initialize() first.")

        self._running = True
        log.info("Arbitrage pipeline starting")

        # Launch connector tasks
        for exchange, connector in self._connectors.items():
            task = asyncio.create_task(
                connector.start(),
                name=f"connector-{exchange}"
            )
            self._tasks.append(task)
            log.info("Connector task launched", exchange=exchange)

        # Status reporter task
        self._tasks.append(
            asyncio.create_task(self._status_reporter(), name="status-reporter")
        )

        log.info(
            "Arbitrage pipeline running",
            task_count=len(self._tasks),
        )

    async def stop(self) -> None:
        """Graceful shutdown."""
        log.info("Stopping arbitrage pipeline")
        self._running = False

        for connector in self._connectors.values():
            await connector.stop()

        for task in self._tasks:
            task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)

        if self._dispatcher:
            await self._dispatcher.stop()

        log.info(
            "Pipeline stopped",
            signals_generated=self._signals_generated,
        )

    async def _on_arbitrage_signal(self, signal: ArbitrageSignal) -> None:
        """Callback: handle new arbitrage signal from engine."""
        self._signals_generated += 1

        # Log to console in structured format
        log.info(
            "ARBITRAGE OPPORTUNITY",
            symbol=signal.symbol,
            buy_on=signal.buy_exchange,
            sell_on=signal.sell_exchange,
            gross_spread_bps=round(signal.spread_bps, 2),
            net_profit_bps=round(signal.net_profit_bps, 2),
            est_profit_usd=round(signal.estimated_profit_usd, 2),
            confidence_pct=round(signal.confidence * 100, 1),
            strength=signal.strength.value,
        )

        # Dispatch alert
        if self._dispatcher:
            await self._dispatcher.dispatch_signal(signal)

    async def _status_reporter(self) -> None:
        """Periodic pipeline health report."""
        while self._running:
            await asyncio.sleep(30)
            if not self._engine:
                continue

            stats = self._engine.get_stats()
            connector_health = {
                ex: {
                    "messages": c.metrics.messages_received,
                    "reconnections": c.metrics.reconnections,
                    "msg_rate": round(c.metrics.message_rate, 1),
                }
                for ex, c in self._connectors.items()
            }

            log.info(
                "Pipeline health",
                ticks_processed=stats["ticks_processed"],
                signals_generated=self._signals_generated,
                price_coverage=stats["price_coverage"],
                connector_health=connector_health,
            )

    def get_current_spreads(self) -> List:
        """Get all current observable spreads (for API/dashboard)."""
        if self._engine:
            return self._engine.get_current_spreads()
        return []

    def get_engine_stats(self) -> Dict:
        if self._engine:
            return self._engine.get_stats()
        return {}


# ─── Standalone Entry Point ───────────────────────────────────────────────────

async def main():
    """Run the arbitrage pipeline as a standalone process."""
    configure_logging(level="INFO", fmt="json")
    log.info("=" * 50)
    log.info("AI Trading Intelligence Platform — Phase 1 MVP")
    log.info("Arbitrage Signal Pipeline")
    log.info("=" * 50)

    pipeline = ArbitragePipeline(
        exchanges=["binance", "coinbase", "kraken"]
    )

    # Graceful shutdown handler
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _handle_signal():
        log.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (os_signal.SIGINT, os_signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except (NotImplementedError, ValueError):
            pass

    try:
        await pipeline.initialize()
        await pipeline.start()
        log.info("Pipeline active. Press Ctrl+C to stop.")
        await shutdown_event.wait()
    finally:
        await pipeline.stop()
        log.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
