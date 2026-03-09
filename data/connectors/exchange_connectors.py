"""
Exchange WebSocket Connectors — Real-time market data ingestion layer.

Each connector handles:
- WebSocket lifecycle (connect, reconnect, heartbeat)
- Message parsing and normalization
- Backpressure and buffering
- Metrics emission
"""
import asyncio
import json
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Callable, Dict, List, Optional, Set
import aiohttp
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.models import PriceTick, OrderBook, OrderBookLevel, OrderSide
from utils.logging import get_logger, log_execution_time

log = get_logger("data.connectors")

# Callback type: async fn(tick: PriceTick) -> None
TickCallback = Callable[[PriceTick], asyncio.Future]


@dataclass
class ConnectorMetrics:
    exchange: str
    messages_received: int = 0
    messages_parsed: int = 0
    parse_errors: int = 0
    reconnections: int = 0
    last_message_ts: Optional[float] = None
    connected_since: Optional[float] = None

    @property
    def uptime_seconds(self) -> float:
        if self.connected_since is None:
            return 0
        return time.time() - self.connected_since

    @property
    def message_rate(self) -> float:
        """Approximate messages/second over uptime."""
        if not self.uptime_seconds:
            return 0
        return self.messages_received / self.uptime_seconds


class ExchangeConnector(ABC):
    """
    Abstract base for all exchange WebSocket connectors.
    Implements: reconnect loop, subscription management, metric tracking.
    """

    RECONNECT_DELAYS = [1, 2, 5, 10, 30, 60]  # exponential-ish backoff

    def __init__(
        self,
        exchange_name: str,
        ws_url: str,
        symbols: List[str],
        on_tick: Optional[TickCallback] = None,
        on_orderbook: Optional[Callable] = None,
    ):
        self.exchange_name = exchange_name
        self.ws_url = ws_url
        self.symbols = symbols
        self._on_tick = on_tick
        self._on_orderbook = on_orderbook
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._reconnect_attempt = 0
        self.metrics = ConnectorMetrics(exchange=exchange_name)
        self._subscribed: Set[str] = set()
        self._price_cache: Dict[str, PriceTick] = {}

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    async def subscribe(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Send subscription messages after connection."""

    @abstractmethod
    def parse_tick(self, raw: Dict) -> Optional[PriceTick]:
        """Normalize exchange-specific message → PriceTick."""

    @abstractmethod
    def parse_orderbook(self, raw: Dict) -> Optional[OrderBook]:
        """Normalize exchange-specific message → OrderBook."""

    @abstractmethod
    def is_heartbeat(self, raw: Dict) -> bool:
        """Return True if message is a keep-alive/ping."""

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the connector with reconnect loop."""
        self._running = True
        log.info("Connector starting", exchange=self.exchange_name,
                 symbols=self.symbols)
        while self._running:
            try:
                await self._connect_and_consume()
                self._reconnect_attempt = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.metrics.reconnections += 1
                delay = self._get_backoff_delay()
                log.error(
                    "Connection error, reconnecting",
                    exchange=self.exchange_name,
                    error=str(e),
                    attempt=self._reconnect_attempt,
                    delay_s=delay,
                )
                await asyncio.sleep(delay)
                self._reconnect_attempt += 1

    async def stop(self) -> None:
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        log.info("Connector stopped", exchange=self.exchange_name)

    async def _connect_and_consume(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=30)
        self._session = aiohttp.ClientSession(timeout=timeout)

        async with self._session.ws_connect(
            self.ws_url,
            heartbeat=20,
            max_msg_size=0,
        ) as ws:
            self._ws = ws
            self.metrics.connected_since = time.time()
            log.info("WebSocket connected", exchange=self.exchange_name)

            await self.subscribe(ws)

            async for msg in ws:
                if not self._running:
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    log.error("WS error", exchange=self.exchange_name,
                              error=str(ws.exception()))
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    log.warning("WS closed by server", exchange=self.exchange_name)
                    break

    async def _handle_message(self, raw_text: str) -> None:
        self.metrics.messages_received += 1
        self.metrics.last_message_ts = time.time()

        try:
            data = json.loads(raw_text)

            if self.is_heartbeat(data):
                return

            tick = self.parse_tick(data)
            if tick:
                self.metrics.messages_parsed += 1
                self._price_cache[tick.symbol] = tick
                if self._on_tick:
                    await self._on_tick(tick)

            ob = self.parse_orderbook(data)
            if ob and self._on_orderbook:
                await self._on_orderbook(ob)

        except json.JSONDecodeError as e:
            self.metrics.parse_errors += 1
            log.debug("JSON parse error", exchange=self.exchange_name, error=str(e))
        except Exception as e:
            self.metrics.parse_errors += 1
            log.error("Message handling error", exchange=self.exchange_name,
                      error=str(e))

    def _get_backoff_delay(self) -> float:
        idx = min(self._reconnect_attempt, len(self.RECONNECT_DELAYS) - 1)
        return float(self.RECONNECT_DELAYS[idx])

    def get_latest_price(self, symbol: str) -> Optional[PriceTick]:
        return self._price_cache.get(symbol)

    def get_all_prices(self) -> Dict[str, PriceTick]:
        return dict(self._price_cache)


# ─── Exchange-Specific Implementations ───────────────────────────────────────

class BinanceConnector(ExchangeConnector):
    """
    Binance Spot WebSocket connector.
    Uses combined stream endpoint for efficient multi-symbol subscriptions.
    """

    WS_BASE = "wss://stream.binance.com:9443/stream?streams="

    def __init__(self, symbols: List[str], **kwargs):
        # Build combined stream URL
        streams = []
        for sym in symbols:
            pair = sym.replace("/", "").lower()
            streams.append(f"{pair}@bookTicker")   # best bid/ask
            streams.append(f"{pair}@depth5@100ms") # order book depth

        ws_url = self.WS_BASE + "/".join(streams)
        super().__init__("binance", ws_url, symbols, **kwargs)

    async def subscribe(self, ws) -> None:
        # Binance combined streams auto-subscribe via URL
        log.info("Binance subscribed via URL streams", count=len(self.symbols))

    def parse_tick(self, raw: Dict) -> Optional[PriceTick]:
        stream = raw.get("stream", "")
        data = raw.get("data", {})

        if "bookTicker" not in stream:
            return None

        # bookTicker: {s: symbol, b: bestBid, B: bestBidQty, a: bestAsk, A: bestAskQty}
        try:
            symbol_raw = data.get("s", "")
            # Convert BTCUSDT → BTC/USDT
            symbol = self._normalize_symbol(symbol_raw)
            bid = float(data["b"])
            ask = float(data["a"])

            return PriceTick(
                exchange="binance",
                symbol=symbol,
                bid=bid,
                ask=ask,
                last=(bid + ask) / 2,
                volume_24h=0.0,
                timestamp=datetime.utcnow(),
                raw=data,
            )
        except (KeyError, ValueError) as e:
            log.debug("Binance tick parse error", error=str(e))
            return None

    def parse_orderbook(self, raw: Dict) -> Optional[OrderBook]:
        stream = raw.get("stream", "")
        data = raw.get("data", {})

        if "depth" not in stream:
            return None

        try:
            # Extract symbol from stream name (btcusdt@depth5@100ms)
            symbol_raw = stream.split("@")[0].upper()
            symbol = self._normalize_symbol(symbol_raw)

            bids = [
                OrderBookLevel(price=float(p), size=float(s), side=OrderSide.BID)
                for p, s in data.get("bids", [])
                if float(s) > 0
            ]
            asks = [
                OrderBookLevel(price=float(p), size=float(s), side=OrderSide.ASK)
                for p, s in data.get("asks", [])
                if float(s) > 0
            ]

            return OrderBook(
                exchange="binance",
                symbol=symbol,
                bids=sorted(bids, key=lambda x: x.price, reverse=True),
                asks=sorted(asks, key=lambda x: x.price),
                timestamp=datetime.utcnow(),
            )
        except Exception as e:
            log.debug("Binance OB parse error", error=str(e))
            return None

    def is_heartbeat(self, raw: Dict) -> bool:
        return "ping" in raw or raw.get("result") is None and "id" in raw

    @staticmethod
    def _normalize_symbol(raw: str) -> str:
        """BTCUSDT → BTC/USDT"""
        raw = raw.upper()
        for quote in ["USDT", "USDC", "BTC", "ETH", "BNB"]:
            if raw.endswith(quote) and len(raw) > len(quote):
                base = raw[: -len(quote)]
                return f"{base}/{quote}"
        return raw


class CoinbaseConnector(ExchangeConnector):
    """
    Coinbase Advanced Trade WebSocket connector.
    """

    def __init__(self, symbols: List[str], **kwargs):
        super().__init__(
            "coinbase",
            "wss://advanced-trade-ws.coinbase.com",
            symbols,
            **kwargs,
        )

    async def subscribe(self, ws) -> None:
        product_ids = [s.replace("/", "-") for s in self.symbols]
        msg = {
            "type": "subscribe",
            "product_ids": product_ids,
            "channel": "ticker",
        }
        await ws.send_json(msg)
        log.info("Coinbase subscribed", products=product_ids)

    def parse_tick(self, raw: Dict) -> Optional[PriceTick]:
        if raw.get("channel") != "ticker":
            return None

        try:
            events = raw.get("events", [])
            for event in events:
                for ticker in event.get("tickers", []):
                    symbol = ticker["product_id"].replace("-", "/")
                    return PriceTick(
                        exchange="coinbase",
                        symbol=symbol,
                        bid=float(ticker.get("best_bid", 0)),
                        ask=float(ticker.get("best_ask", 0)),
                        last=float(ticker.get("price", 0)),
                        volume_24h=float(ticker.get("volume_24_h", 0)),
                        timestamp=datetime.utcnow(),
                        raw=ticker,
                    )
        except (KeyError, ValueError) as e:
            log.debug("Coinbase tick parse error", error=str(e))
        return None

    def parse_orderbook(self, raw: Dict) -> Optional[OrderBook]:
        return None  # Handled via separate level2 subscription

    def is_heartbeat(self, raw: Dict) -> bool:
        return raw.get("channel") == "heartbeats"


class KrakenConnector(ExchangeConnector):
    """Kraken WebSocket v2 connector."""

    def __init__(self, symbols: List[str], **kwargs):
        super().__init__(
            "kraken",
            "wss://ws.kraken.com/v2",
            symbols,
            **kwargs,
        )

    async def subscribe(self, ws) -> None:
        pairs = [s.replace("/", "") for s in self.symbols]
        msg = {
            "method": "subscribe",
            "params": {
                "channel": "ticker",
                "symbol": [s.replace("USDT", "/USDT") for s in pairs],
            }
        }
        await ws.send_json(msg)

    def parse_tick(self, raw: Dict) -> Optional[PriceTick]:
        if raw.get("channel") != "ticker":
            return None
        try:
            data = raw.get("data", [{}])[0]
            symbol = data.get("symbol", "").replace("/", "/")
            return PriceTick(
                exchange="kraken",
                symbol=symbol,
                bid=float(data.get("bid", 0)),
                ask=float(data.get("ask", 0)),
                last=float(data.get("last", 0)),
                volume_24h=float(data.get("volume", 0)),
                timestamp=datetime.utcnow(),
                raw=data,
            )
        except (KeyError, ValueError, IndexError):
            return None

    def parse_orderbook(self, raw: Dict) -> Optional[OrderBook]:
        return None

    def is_heartbeat(self, raw: Dict) -> bool:
        return raw.get("channel") == "heartbeat" or raw.get("method") == "pong"


# ─── Connector Registry ───────────────────────────────────────────────────────

CONNECTOR_MAP = {
    "binance": BinanceConnector,
    "coinbase": CoinbaseConnector,
    "kraken": KrakenConnector,
}


def build_connector(exchange: str, symbols: List[str], **callbacks) -> ExchangeConnector:
    cls = CONNECTOR_MAP.get(exchange)
    if not cls:
        raise ValueError(f"No connector for exchange: {exchange}")
    return cls(symbols=symbols, **callbacks)
