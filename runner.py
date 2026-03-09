"""
QuantEdge — Combined Pipeline Runner (Service 2)

Runs all active signal pipelines concurrently in a single process:
  - Phase 1: Arbitrage Pipeline (Binance, Coinbase, Kraken WebSockets)
  - Phase 2: Sentiment Pipeline (Reddit, CryptoPanic, Fear & Greed)

A single /health endpoint reports status of both pipelines.
"""
import asyncio
import os
import signal as os_signal
import sys

from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from signals.arbitrage.pipeline import ArbitragePipeline
from signals.sentiment.pipeline import SentimentPipeline
from utils.logging import configure_logging, get_logger

log = get_logger("platform.runner")


async def health_server(arb_pipeline: ArbitragePipeline, sent_pipeline: SentimentPipeline) -> None:
    """Combined health endpoint for both pipelines."""

    async def handle(request):
        arb_stats = arb_pipeline.get_engine_stats()
        fgi = sent_pipeline._fear_greed.latest
        return web.json_response({
            "status": "ok",
            "pipelines": {
                "arbitrage": {
                    "active": arb_pipeline._running,
                    "ticks_processed": arb_stats.get("ticks_processed", 0),
                    "signals_generated": arb_pipeline._signals_generated,
                },
                "sentiment": {
                    "active": sent_pipeline._running,
                    "posts_processed": sent_pipeline._engine._posts_processed,
                    "signals_generated": sent_pipeline._signals_generated,
                    "tokens_tracked": len(sent_pipeline._engine._mention_windows),
                    "fear_greed": fgi.value if fgi else None,
                    "fear_greed_label": fgi.classification if fgi else "unknown",
                },
            },
        })

    port = int(os.getenv("PORT", "8000"))
    app = web.Application()
    app.router.add_get("/health", handle)
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Combined health server running", port=port)


async def main():
    configure_logging(level="INFO", fmt="json")

    log.info("=" * 60)
    log.info("QuantEdge AI Trading Intelligence Platform")
    log.info("Phase 1: Arbitrage  |  Phase 2: Sentiment")
    log.info("=" * 60)

    arb_pipeline = ArbitragePipeline(exchanges=["binance", "coinbase", "kraken"])
    sent_pipeline = SentimentPipeline()

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
        # Initialize both pipelines
        await arb_pipeline.initialize()
        await sent_pipeline.initialize()

        # Start both pipelines
        await arb_pipeline.start()
        await sent_pipeline.start()

        # Start combined health server
        asyncio.create_task(health_server(arb_pipeline, sent_pipeline))

        log.info("All pipelines active.")
        await shutdown_event.wait()

    finally:
        await asyncio.gather(
            arb_pipeline.stop(),
            sent_pipeline.stop(),
            return_exceptions=True,
        )
        log.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
