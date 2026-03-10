"""
Order Book Collector — Phase 4

Streams real-time order book depth from Binance WebSocket.
Feeds LiquidityEngine with snapshots for wall + imbalance detection.

Binance provides free, public depth streams — no API key needed.
"""
import asyncio
import os
import json
from datetime import datetime
from typing import Callable, List, Optional

import aiohttp

from core.models import OrderBook, OrderBookLevel
from utils.logging import get_logger

log = get_logger("ingestion.orderbook")

# Symbols to track for liquidity walls
TRACKED_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
]

# Binance symbol → standard symbol mapping
SYMBOL_MAP = {
    "BTCUSDT": "BTC/USDT",
    "ETHUSDT": "ETH/USDT",
    "SOLUSDT": "SOL/USDT",
    "BNBUSDT": "BNB/USDT",
    "XRPUSDT": "XRP/USDT",
    "ADAUSDT": "ADA/USDT",
    "AVAXUSDT": "AVAX/USDT",
    "LINKUSDT": "LINK/USDT",
}


class BinanceOrderBookCollector:
    """
    Streams Binance order book depth via WebSocket.
    Uses the combined stream endpoint for multiple symbols efficiently.
    Emits OrderBook snapshots to the LiquidityEngine.
    """

    WS_BASE = "wss://stream.binance.com:9443/stream"
    DEPTH_LEVELS = 20  # top 20 bids/asks

    def __init__(
        self,
        symbols: List[str] = TRACKED_SYMBOLS,
        on_orderbook: Optional[Callable] = None,
        update_interval_ms: int = 1000,
    ):
        self.symbols = symbols
        self._on_orderbook = on_orderbook
        self._update_interval_ms = update_interval_ms
        self._running = False
        self._snapshots_processed = 0

    async def start(self, session: aiohttp.ClientSession) -> None:
        self._running = True
        log.info("Order book collector starting", symbols=len(self.symbols))

        while self._running:
            try:
                await self._connect(session)
            except Exception as e:
                log.warning("Order book WS disconnected", error=str(e))
                await asyncio.sleep(5)

    async def stop(self):
        self._running = False

    async def _connect(self, session: aiohttp.ClientSession) -> None:
        # Build combined stream: btcusdt@depth20@1000ms/ethusdt@depth20@1000ms/...
        streams = "/".join(
            f"{s.lower()}@depth{self.DEPTH_LEVELS}@{self._update_interval_ms}ms"
            for s in self.symbols
        )
        url = f"{self.WS_BASE}?streams={streams}"

        async with session.ws_connect(url, heartbeat=30) as ws:
            log.info("Order book WebSocket connected", streams=len(self.symbols))

            async for msg in ws:
                if not self._running:
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

    async def _handle_message(self, raw: str) -> None:
        try:
            envelope = json.loads(raw)
            stream_name = envelope.get("stream", "")
            data = envelope.get("data", {})

            # Extract symbol from stream name (e.g. "btcusdt@depth20@1000ms")
            binance_symbol = stream_name.split("@")[0].upper()
            symbol = SYMBOL_MAP.get(binance_symbol, binance_symbol)

            bids_raw = data.get("bids", [])
            asks_raw = data.get("asks", [])

            if not bids_raw or not asks_raw:
                return

            bids = [
                OrderBookLevel(price=float(b[0]), size=float(b[1]))
                for b in bids_raw
            ]
            asks = [
                OrderBookLevel(price=float(a[0]), size=float(a[1]))
                for a in asks_raw
            ]

            ob = OrderBook(
                exchange="binance",
                symbol=symbol,
                bids=sorted(bids, key=lambda x: x.price, reverse=True),
                asks=sorted(asks, key=lambda x: x.price),
                timestamp=datetime.utcnow(),
            )

            self._snapshots_processed += 1

            if self._on_orderbook:
                await self._on_orderbook(ob)

        except Exception as e:
            log.debug("Order book parse error", error=str(e))
