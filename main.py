"""
Platform Orchestrator — Full system coordinator.

Brings up all signal pipelines, the API server, and alert dispatcher.
Designed to run as the main entry point for the complete platform.
"""
import asyncio
import uvicorn
import signal as os_signal
from typing import List
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import get_platform
from signals.arbitrage.pipeline import ArbitragePipeline
from alerts.dispatcher import AlertDispatcher
from api.routes import app as api_app, inject_pipeline, record_signal
from core.models import BaseSignal
from utils.logging import configure_logging, get_logger

log = get_logger("platform.orchestrator")


class TradingPlatform:
    """
    Master orchestrator for the AI Trading Intelligence Platform.

    Manages lifecycle of all subsystems:
    - ArbitragePipeline (Phase 1)
    - SentimentPipeline (Phase 3)
    - LiquidityEngine   (Phase 4)
    - WhaleEngine       (Phase 5)
    - API Server
    - Alert Dispatcher
    """

    def __init__(self):
        self.config = get_platform()
        self._arbitrage_pipeline: ArbitragePipeline = None
        self._tasks: List[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        self._running = True
        log.info(
            "=" * 60,
        )
        log.info("AI Trading Intelligence Platform — Starting Up")
        log.info("=" * 60)

        # Phase 1: Arbitrage Pipeline
        self._arbitrage_pipeline = ArbitragePipeline(
            exchanges=["binance", "coinbase", "kraken"]
        )
        await self._arbitrage_pipeline.initialize()
        inject_pipeline(self._arbitrage_pipeline)
        await self._arbitrage_pipeline.start()

        # Start API server
        self._tasks.append(
            asyncio.create_task(self._run_api_server(), name="api-server")
        )

        log.info(
            "Platform fully operational",
            api_port=self.config.api_port,
            env=self.config.env,
        )

    async def stop(self) -> None:
        log.info("Platform shutting down...")
        self._running = False

        if self._arbitrage_pipeline:
            await self._arbitrage_pipeline.stop()

        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        log.info("Platform stopped.")

    async def _run_api_server(self) -> None:
        """Run FastAPI server as async task."""
        config = uvicorn.Config(
            app=api_app,
            host=self.config.api_host,
            port=self.config.api_port,
            log_level="warning",
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        await server.serve()


async def main():
    configure_logging(level="INFO", fmt="json")

    platform = TradingPlatform()
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
        await platform.start()
        log.info("Platform running. Press Ctrl+C to stop.")
        await shutdown_event.wait()
    finally:
        await platform.stop()


if __name__ == "__main__":
    asyncio.run(main())
