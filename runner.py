"""
QuantEdge — Combined Pipeline Runner (Service 2)

Runs all active signal pipelines concurrently in a single process:
  - Phase 1: Arbitrage  (Binance, Coinbase, Kraken WebSockets)
  - Phase 2: Sentiment  (Reddit, CryptoPanic, Fear & Greed)
  - Phase 3: Whale      (Etherscan ETH/ERC-20, Bitcoin blockchain.info)
  - Phase 4: Liquidity  (Binance order book depth streams)

A single /health endpoint reports status of all pipelines.
"""
import asyncio
import os
import signal as os_signal
import sys

from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from signals.arbitrage.pipeline import ArbitragePipeline
from signals.liquidity.pipeline import LiquidityPipeline
from signals.sentiment.pipeline import SentimentPipeline
from signals.whale.pipeline import WhalePipeline
from utils.logging import configure_logging, get_logger

log = get_logger("platform.runner")


async def health_server(arb, sent, whale, liq) -> None:
    async def handle(request):
        fgi = sent._fear_greed.latest
        return web.json_response({
            "status": "ok",
            "pipelines": {
                "arbitrage": {
                    "active": arb._running,
                    "signals_generated": arb._signals_generated,
                },
                "sentiment": {
                    "active": sent._running,
                    "posts_processed": sent._engine._posts_processed,
                    "signals_generated": sent._signals_generated,
                    "tokens_tracked": len(sent._engine._mention_windows),
                    "fear_greed": fgi.value if fgi else None,
                    "fear_greed_label": fgi.classification if fgi else "unknown",
                },
                "whale": {
                    "active": whale._running,
                    "tx_processed": whale._tx_processed,
                    "signals_generated": whale._signals_generated,
                },
                "liquidity": {
                    "active": liq._running,
                    "snapshots_processed": liq._snapshots_processed,
                    "signals_generated": liq._signals_generated,
                    "symbols_tracked": len(liq._engine._ob_stats),
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
    log.info("Health server running", port=port)


async def main():
    configure_logging(level="INFO", fmt="json")

    log.info("=" * 60)
    log.info("QuantEdge AI Trading Intelligence Platform")
    log.info("Phase 1: Arbitrage | Phase 2: Sentiment")
    log.info("Phase 3: Whale     | Phase 4: Liquidity")
    log.info("=" * 60)

    arb   = ArbitragePipeline(exchanges=["binance", "coinbase", "kraken"])
    sent  = SentimentPipeline()
    whale = WhalePipeline()
    liq   = LiquidityPipeline()

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
        await arb.initialize()
        await sent.initialize()
        await whale.initialize()
        await liq.initialize()

        await arb.start()
        await sent.start()
        await whale.start()
        await liq.start()

        asyncio.create_task(health_server(arb, sent, whale, liq))

        log.info("All pipelines active.")
        await shutdown_event.wait()

    finally:
        await asyncio.gather(
            arb.stop(), sent.stop(), whale.stop(), liq.stop(),
            return_exceptions=True,
        )
        log.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
