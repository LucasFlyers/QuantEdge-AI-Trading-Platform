"""
Sentiment Signal Pipeline — Phase 2

Orchestrates:
  Reddit + CryptoPanic → SentimentEngine → AlertDispatcher → Telegram

Run standalone:
  PYTHONPATH=/app python signals/sentiment/pipeline.py

Or added to main platform orchestrator alongside the arbitrage pipeline.
"""
import asyncio
import os
import signal as os_signal
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from alerts.dispatcher import AlertDispatcher
from data.ingestion.social_collectors import (
    CryptoPanicCollector,
    FearGreedCollector,
    RedditCollector,
    SocialPost,
)
from data.storage.neon import get_repository, init_pool, run_migrations
from config.settings import get_neon
from signals.sentiment.engine import SentimentEngine
from core.models import SentimentSignal
from utils.logging import configure_logging, get_logger

log = get_logger("pipeline.sentiment")


class SentimentPipeline:
    """
    Phase 2: Real-time social sentiment signal pipeline.

    Architecture:
      [Reddit Collector]      ─┐
      [CryptoPanic Collector] ─┤─→ [SentimentEngine] ─→ [AlertDispatcher]
      [Fear & Greed Index]    ─┘         │
                                    [Neon DB]
    """

    def __init__(self):
        self._engine = SentimentEngine(on_signal=self._on_sentiment_signal)
        self._dispatcher = AlertDispatcher()
        self._fear_greed = FearGreedCollector(interval_s=3600)
        self._running = False
        self._signals_generated = 0
        self._session: aiohttp.ClientSession | None = None

    async def initialize(self) -> None:
        log.info("Initializing sentiment pipeline")

        # Start alert dispatcher
        await self._dispatcher.start()
        log.info("Alert dispatcher started")

        # Connect to Neon if configured
        cfg = get_neon()
        if cfg.is_configured:
            await init_pool()
            await run_migrations()
            log.info("Neon DB connected")
        else:
            log.warning("DATABASE_URL not set — signals won't be persisted")

        log.info("Sentiment pipeline initialized")

    async def start(self) -> None:
        self._running = True
        self._session = aiohttp.ClientSession()

        log.info("Sentiment pipeline starting")

        # Wire up collectors → engine
        reddit = RedditCollector(interval_s=60, on_post=self._engine.process_post)
        cryptopanic = CryptoPanicCollector(interval_s=120, on_post=self._engine.process_post)

        # Launch all collectors concurrently
        tasks = [
            asyncio.create_task(reddit.start(self._session), name="reddit"),
            asyncio.create_task(cryptopanic.start(self._session), name="cryptopanic"),
            asyncio.create_task(self._fear_greed.start(self._session), name="fear-greed"),
            asyncio.create_task(self._baseline_updater(), name="baseline-updater"),
            asyncio.create_task(self._status_reporter(), name="status-reporter"),
        ]

        self._tasks = tasks
        log.info("Sentiment pipeline running", task_count=len(tasks))

    async def stop(self) -> None:
        log.info("Stopping sentiment pipeline")
        self._running = False

        for task in getattr(self, "_tasks", []):
            task.cancel()
        await asyncio.gather(*getattr(self, "_tasks", []), return_exceptions=True)

        if self._session:
            await self._session.close()

        await self._dispatcher.stop()
        log.info("Sentiment pipeline stopped", signals_generated=self._signals_generated)

    async def _on_sentiment_signal(self, signal: SentimentSignal) -> None:
        """Handle a new sentiment signal — persist + dispatch."""
        self._signals_generated += 1

        # Apply Fear & Greed macro bias to confidence
        macro_bias = self._fear_greed.macro_bias
        fgi = self._fear_greed.latest

        log.info(
            "SENTIMENT SIGNAL",
            token=signal.token,
            direction=signal.direction.value,
            strength=signal.strength.value,
            sentiment_score=round(signal.sentiment_score, 3),
            mention_count=signal.mention_count,
            mention_change_pct=round(signal.mention_change_pct, 1),
            confidence=round(signal.confidence, 3),
            macro_bias=round(macro_bias, 2),
            fear_greed=fgi.classification if fgi else "unknown",
        )

        # Persist to Neon
        cfg = get_neon()
        if cfg.is_configured:
            try:
                repo = get_repository()
                await repo.save_sentiment(signal)
            except Exception as e:
                log.error("Failed to persist sentiment signal", error=str(e))

        # Dispatch alert
        await self._dispatcher.dispatch_signal(signal)

    async def _baseline_updater(self) -> None:
        """Update mention rate baselines every minute."""
        while self._running:
            await asyncio.sleep(60)
            for token in list(self._engine._mention_windows.keys()):
                self._engine.update_baseline(token)

    async def _status_reporter(self) -> None:
        """Log pipeline health every 5 minutes."""
        while self._running:
            await asyncio.sleep(300)
            fgi = self._fear_greed.latest
            log.info(
                "Sentiment pipeline health",
                posts_processed=self._engine._posts_processed,
                signals_generated=self._signals_generated,
                tokens_tracked=len(self._engine._mention_windows),
                fear_greed_value=fgi.value if fgi else None,
                fear_greed_label=fgi.classification if fgi else "unknown",
            )


# ─── Standalone Entry Point ───────────────────────────────────────────────────

async def main():
    configure_logging(level="INFO", fmt="json")

    log.info("=" * 50)
    log.info("QuantEdge — Phase 2: Sentiment Intelligence")
    log.info("=" * 50)

    pipeline = SentimentPipeline()

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

        # Health server so Railway healthcheck passes
        from aiohttp import web

        async def handle_health(request):
            fgi = pipeline._fear_greed.latest
            return web.json_response({
                "status": "ok",
                "pipeline": "sentiment",
                "posts_processed": pipeline._engine._posts_processed,
                "signals_generated": pipeline._signals_generated,
                "tokens_tracked": len(pipeline._engine._mention_windows),
                "fear_greed": fgi.value if fgi else None,
            })

        health_app = web.Application()
        health_app.router.add_get("/health", handle_health)
        health_app.router.add_get("/", handle_health)
        runner = web.AppRunner(health_app)
        await runner.setup()
        port = int(os.getenv("PORT", "8000"))
        await web.TCPSite(runner, "0.0.0.0", port).start()
        log.info("Health server running", port=port)

        log.info("Sentiment pipeline active.")
        await shutdown_event.wait()
    finally:
        await pipeline.stop()
        log.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
