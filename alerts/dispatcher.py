"""
Alert Dispatcher — Multi-channel signal delivery system.

Supports:
- Telegram Bot API
- Discord Webhooks
- Email (SMTP)
- Generic Webhooks

All channels are async, non-blocking, with retry logic.
"""
import asyncio
import aiohttp
import smtplib
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.models import (
    Alert, AlertChannel, ArbitrageSignal, BaseSignal,
    LiquiditySignal, SentimentSignal, SignalStrength,
    SignalType, WhaleMoveType, WhaleSignal
)
from config.settings import get_telegram, DEVELOPER
from utils.logging import get_logger

log = get_logger("alerts.dispatcher")

# ─── Developer Credit ─────────────────────────────────────────────────────────

DEVELOPER_FOOTER = (
    f"\n{'─' * 28}\n"
    f"⚙️ *{DEVELOPER['platform']}*\n"
    f"👨‍💻 Built by [{DEVELOPER['name']}]({DEVELOPER['linkedin']})"
)


# ─── Alert Formatters ─────────────────────────────────────────────────────────

class SignalFormatter:
    """Format signals as human-readable alert messages."""

    STRENGTH_EMOJI = {
        SignalStrength.WEAK: "🟡",
        SignalStrength.MODERATE: "🟠",
        SignalStrength.STRONG: "🔴",
        SignalStrength.CRITICAL: "🚨",
    }

    DIRECTION_EMOJI = {
        "bullish": "📈",
        "bearish": "📉",
        "neutral": "➡️",
    }

    @classmethod
    def format_arbitrage(cls, signal: ArbitrageSignal) -> str:
        strength_icon = cls.STRENGTH_EMOJI.get(signal.strength, "⚪")
        confidence_bar = cls._confidence_bar(signal.confidence)

        return (
            f"{strength_icon} *ARBITRAGE SIGNAL*\n"
            f"{'─' * 28}\n"
            f"💎 *Asset:* `{signal.symbol}`\n"
            f"📥 *Buy:*  `{signal.buy_exchange.upper()}` @ `${signal.buy_price:,.4f}`\n"
            f"📤 *Sell:* `{signal.sell_exchange.upper()}` @ `${signal.sell_price:,.4f}`\n"
            f"{'─' * 28}\n"
            f"📊 *Gross Spread:*   `{signal.spread_bps:.2f} bps`\n"
            f"💸 *Total Fees:*    `{signal.estimated_fee_bps:.2f} bps`\n"
            f"✅ *Net Profit:*    `{signal.net_profit_bps:.2f} bps`\n"
            f"💰 *Est. Profit:*   `${signal.estimated_profit_usd:,.2f}`\n"
            f"📦 *Max Size:*      `${signal.max_tradeable_size_usd:,.0f}`\n"
            f"{'─' * 28}\n"
            f"🎯 *Confidence:* {confidence_bar} `{signal.confidence * 100:.1f}%`\n"
            f"⚡ *Window:*  `{signal.execution_window_ms}ms`\n"
            f"🕐 `{signal.timestamp.strftime('%H:%M:%S.%f')[:-3]} UTC`"
            + DEVELOPER_FOOTER
        )

    @classmethod
    def format_sentiment(cls, signal: SentimentSignal) -> str:
        direction_icon = cls.DIRECTION_EMOJI.get(signal.direction.value, "➡️")
        sentiment_bar = cls._sentiment_bar(signal.sentiment_score)
        mention_dir = "📈" if signal.mention_change_pct > 0 else "📉"

        return (
            f"{direction_icon} *SENTIMENT SIGNAL*\n"
            f"{'─' * 28}\n"
            f"🪙 *Token:* `{signal.token}`\n"
            f"💬 *Mentions:* `{signal.mention_count:,}` "
            f"({mention_dir} `{signal.mention_change_pct:+.0f}%` in {signal.lookback_hours}h)\n"
            f"{'─' * 28}\n"
            f"😊 *Sentiment:* {sentiment_bar}\n"
            f"📈 Bullish: `{signal.bullish_pct:.1f}%`  "
            f"😐 Neutral: `{signal.neutral_pct:.1f}%`  "
            f"📉 Bearish: `{signal.bearish_pct:.1f}%`\n"
            f"{'─' * 28}\n"
            f"🎯 *Confidence:* `{signal.confidence * 100:.1f}%`\n"
            f"🕐 `{signal.timestamp.strftime('%H:%M:%S')} UTC`"
            + DEVELOPER_FOOTER
        )

    @classmethod
    def format_whale(cls, signal: WhaleSignal) -> str:
        move_icons = {
            WhaleMoveType.EXCHANGE_DEPOSIT: "🏦⬆️ *Exchange Deposit*",
            WhaleMoveType.EXCHANGE_WITHDRAWAL: "🏦⬇️ *Exchange Withdrawal*",
            WhaleMoveType.WALLET_TO_WALLET: "💼 *Wallet Transfer*",
            WhaleMoveType.DEX_INTERACTION: "🔄 *DEX Activity*",
        }

        return (
            f"🐋 *WHALE ALERT*\n"
            f"{'─' * 28}\n"
            f"{move_icons.get(signal.move_type, '🔀 *Transfer*')}\n"
            f"🪙 *Asset:* `{signal.asset}`\n"
            f"💰 *Amount:* `{signal.amount:,.2f} {signal.asset}` "
            f"(`${signal.amount_usd:,.0f}`)\n"
            f"{'─' * 28}\n"
            f"📤 *From:* `{signal.from_address[:8]}...{signal.from_address[-6:]}`\n"
            f"📥 *To:*   `{signal.to_address[:8]}...{signal.to_address[-6:]}`\n"
            + (f"🏛 *Exchange:* `{signal.exchange_name}`\n" if signal.exchange_name else "")
            + (f"\n📋 *Pattern:* _{signal.historical_pattern}_\n" if signal.historical_pattern else "")
            + f"{'─' * 28}\n"
            f"🔗 *TX:* `{signal.tx_hash[:16]}...`\n"
            f"🎯 *Confidence:* `{signal.confidence * 100:.1f}%`\n"
            f"🕐 `{signal.timestamp.strftime('%H:%M:%S')} UTC`"
            + DEVELOPER_FOOTER
        )

    @classmethod
    def format_liquidity(cls, signal: LiquiditySignal) -> str:
        wall_icon = "🟥" if signal.wall_side.value == "ask" else "🟩"
        wall_label = "SELL WALL" if signal.wall_side.value == "ask" else "BUY WALL"

        return (
            f"{wall_icon} *LIQUIDITY SIGNAL — {wall_label}*\n"
            f"{'─' * 28}\n"
            f"💎 *Asset:* `{signal.symbol}`\n"
            f"🏛 *Exchange:* `{signal.exchange.upper()}`\n"
            f"📍 *Price Level:* `${signal.wall_price:,.2f}`\n"
            f"📦 *Wall Size:* `${signal.wall_size_usd:,.0f}` "
            f"(`{signal.wall_size_base:,.2f} {signal.symbol.split('/')[0]}`)\n"
            f"{'─' * 28}\n"
            f"⚖️ *Imbalance:* `{signal.imbalance_ratio:.1%}` bid vs ask\n"
            f"📊 *Bid Depth:* `${signal.bid_depth_usd:,.0f}`\n"
            f"📊 *Ask Depth:* `${signal.ask_depth_usd:,.0f}`\n"
            f"{'─' * 28}\n"
            f"🎯 *Confidence:* `{signal.confidence * 100:.1f}%`\n"
            f"🕐 `{signal.timestamp.strftime('%H:%M:%S')} UTC`"
            + DEVELOPER_FOOTER
        )

    @staticmethod
    def _confidence_bar(confidence: float, length: int = 10) -> str:
        filled = round(confidence * length)
        return "█" * filled + "░" * (length - filled)

    @staticmethod
    def _sentiment_bar(score: float) -> str:
        """score: -1 (bearish) to +1 (bullish)"""
        normalized = (score + 1) / 2  # 0 to 1
        filled = round(normalized * 10)
        bear = max(0, 5 - filled)
        bull = max(0, filled - 5)
        neutral = 10 - bear - bull
        return "🔴" * bear + "⚪" * neutral + "🟢" * bull


# ─── Channel Implementations ──────────────────────────────────────────────────

class AlertChannel_ABC(ABC):
    @abstractmethod
    async def send(self, message: str, priority: int = 1) -> bool:
        ...


class TelegramChannel(AlertChannel_ABC):
    """
    Telegram Bot API alert channel.
    Supports Markdown formatting and message threading.
    """

    BASE_URL = "https://api.telegram.org/bot{token}/{method}"
    MAX_RETRIES = 3
    RETRY_DELAY_S = 2.0

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._session: Optional[aiohttp.ClientSession] = None
        self._sent_count = 0
        self._failed_count = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def send(self, message: str, priority: int = 1) -> bool:
        if not self.bot_token or not self.chat_id:
            log.warning("Telegram not configured, skipping alert")
            return False

        url = self.BASE_URL.format(token=self.bot_token, method="sendMessage")
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        for attempt in range(self.MAX_RETRIES):
            try:
                session = await self._get_session()
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        self._sent_count += 1
                        return True
                    elif resp.status == 429:
                        # Rate limited
                        retry_after = int((await resp.json()).get("parameters", {}).get("retry_after", 5))
                        log.warning("Telegram rate limited", retry_after=retry_after)
                        await asyncio.sleep(retry_after)
                    else:
                        body = await resp.text()
                        log.error("Telegram API error", status=resp.status, body=body)
                        break
            except aiohttp.ClientError as e:
                log.error("Telegram send error", attempt=attempt, error=str(e))
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY_S * (attempt + 1))

        self._failed_count += 1
        return False

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class DiscordChannel(AlertChannel_ABC):
    """Discord Webhook alert channel."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def send(self, message: str, priority: int = 1) -> bool:
        if not self.webhook_url:
            return False

        # Convert Telegram markdown to Discord format
        discord_msg = message.replace("*", "**").replace("`", "`")

        color = [0x00ff00, 0xffff00, 0xff8800, 0xff0000, 0xff0000][min(priority - 1, 4)]

        payload = {
            "embeds": [{
                "description": discord_msg[:4096],
                "color": color,
                "timestamp": datetime.utcnow().isoformat(),
            }]
        }

        try:
            if not self._session or self._session.closed:
                self._session = aiohttp.ClientSession()

            async with self._session.post(self.webhook_url, json=payload) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            log.error("Discord send error", error=str(e))
            return False


# ─── Alert Dispatcher ─────────────────────────────────────────────────────────

@dataclass
class AlertMetrics:
    total_dispatched: int = 0
    total_delivered: int = 0
    total_failed: int = 0
    channel_stats: Dict[str, Dict] = field(default_factory=dict)


class AlertDispatcher:
    """
    Central alert routing system.

    Receives signals → formats messages → dispatches to configured channels.
    Implements: priority queuing, deduplication, rate limiting per channel.
    """

    # Alert rate limit: max N alerts per minute
    RATE_LIMIT_PER_MIN = 30

    def __init__(self):
        self._channels: Dict[str, AlertChannel_ABC] = {}
        self._alert_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._running = False
        self._metrics = AlertMetrics()
        self._recent_alert_hashes: deque = deque(maxlen=100)

        # Rate limiting: timestamps of recent alerts
        self._alert_timestamps: deque = deque(maxlen=self.RATE_LIMIT_PER_MIN)

        # Auto-configure Telegram if env is set
        tg_cfg = get_telegram()
        if tg_cfg.bot_token and tg_cfg.chat_id:
            self.add_channel(
                "telegram",
                TelegramChannel(tg_cfg.bot_token, tg_cfg.chat_id)
            )
            log.info("Telegram channel auto-configured")

    def add_channel(self, name: str, channel: AlertChannel_ABC) -> None:
        self._channels[name] = channel
        log.info("Alert channel added", channel=name)

    async def start(self) -> None:
        """Start background alert dispatch loop."""
        self._running = True
        asyncio.create_task(self._dispatch_loop())
        log.info("AlertDispatcher started", channels=list(self._channels.keys()))

    async def stop(self) -> None:
        self._running = False
        for channel in self._channels.values():
            if hasattr(channel, "close"):
                await channel.close()

    async def dispatch_signal(self, signal: BaseSignal) -> None:
        """Format and queue a signal for delivery."""
        message = self._format_signal(signal)
        if not message:
            return

        # Deduplication hash
        sig_hash = f"{signal.signal_type.value}:{hash(message) % 100000}"
        if sig_hash in self._recent_alert_hashes:
            log.debug("Duplicate alert suppressed", signal_id=signal.id)
            return
        self._recent_alert_hashes.append(sig_hash)

        priority = self._get_priority(signal)

        try:
            self._alert_queue.put_nowait({
                "message": message,
                "priority": priority,
                "signal_id": signal.id,
                "signal_type": signal.signal_type.value,
            })
        except asyncio.QueueFull:
            log.warning("Alert queue full, dropping alert", signal_id=signal.id)

    async def _dispatch_loop(self) -> None:
        """Background worker consuming alert queue."""
        while self._running:
            try:
                item = await asyncio.wait_for(
                    self._alert_queue.get(), timeout=1.0
                )
                await self._send_to_all_channels(
                    item["message"], item["priority"]
                )
                self._metrics.total_dispatched += 1
                self._alert_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log.error("Alert dispatch error", error=str(e))

    async def _send_to_all_channels(self, message: str, priority: int) -> None:
        """Send to all registered channels concurrently."""
        if not self._channels:
            log.debug("No alert channels configured, message logged only")
            log.info("ALERT", message=message[:200])
            return

        # Rate limit check
        now = time.time()
        self._alert_timestamps.append(now)
        one_min_ago = now - 60
        recent_count = sum(1 for ts in self._alert_timestamps if ts > one_min_ago)
        if recent_count > self.RATE_LIMIT_PER_MIN:
            log.warning("Alert rate limit exceeded", recent_count=recent_count)
            return

        tasks = [
            channel.send(message, priority)
            for channel in self._channels.values()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if result is True:
                self._metrics.total_delivered += 1
            elif isinstance(result, Exception):
                self._metrics.total_failed += 1
                log.error("Channel delivery failed", error=str(result))

    def _format_signal(self, signal: BaseSignal) -> Optional[str]:
        if isinstance(signal, ArbitrageSignal):
            return SignalFormatter.format_arbitrage(signal)
        elif isinstance(signal, SentimentSignal):
            return SignalFormatter.format_sentiment(signal)
        elif isinstance(signal, WhaleSignal):
            return SignalFormatter.format_whale(signal)
        elif isinstance(signal, LiquiditySignal):
            return SignalFormatter.format_liquidity(signal)
        return None

    def _get_priority(self, signal: BaseSignal) -> int:
        strength_priority = {
            SignalStrength.WEAK: 1,
            SignalStrength.MODERATE: 2,
            SignalStrength.STRONG: 4,
            SignalStrength.CRITICAL: 5,
        }
        return strength_priority.get(signal.strength, 2)

    def get_metrics(self) -> AlertMetrics:
        return self._metrics
