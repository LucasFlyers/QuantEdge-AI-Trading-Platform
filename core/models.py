"""
Core Domain Models — Immutable typed data contracts for the entire platform.
All inter-service communication uses these models.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any
import uuid


# ─── Enumerations ─────────────────────────────────────────────────────────────

class SignalType(str, Enum):
    ARBITRAGE = "arbitrage"
    SENTIMENT = "sentiment"
    LIQUIDITY = "liquidity"
    WHALE = "whale"
    ANOMALY = "anomaly"


class SignalDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SignalStrength(str, Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    CRITICAL = "critical"


class AlertChannel(str, Enum):
    TELEGRAM = "telegram"
    DISCORD = "discord"
    EMAIL = "email"
    WEBHOOK = "webhook"


class OrderSide(str, Enum):
    BID = "bid"
    ASK = "ask"


class WhaleMoveType(str, Enum):
    EXCHANGE_DEPOSIT = "exchange_deposit"
    EXCHANGE_WITHDRAWAL = "exchange_withdrawal"
    WALLET_TO_WALLET = "wallet_to_wallet"
    DEX_INTERACTION = "dex_interaction"


# ─── Market Data Models ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class PriceTick:
    exchange: str
    symbol: str
    bid: float
    ask: float
    last: float
    volume_24h: float
    timestamp: datetime
    raw: Optional[Dict[str, Any]] = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        return ((self.ask - self.bid) / self.mid) * 10_000


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    size: float
    side: OrderSide


@dataclass
class OrderBook:
    exchange: str
    symbol: str
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]
    timestamp: datetime
    sequence: Optional[int] = None

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def bid_depth(self) -> float:
        return sum(level.size * level.price for level in self.bids)

    @property
    def ask_depth(self) -> float:
        return sum(level.size * level.price for level in self.asks)

    @property
    def imbalance_ratio(self) -> float:
        total = self.bid_depth + self.ask_depth
        if total == 0:
            return 0.5
        return self.bid_depth / total


# ─── Signal Models ────────────────────────────────────────────────────────────

@dataclass
class BaseSignal:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    signal_type: SignalType = SignalType.ARBITRAGE
    direction: SignalDirection = SignalDirection.NEUTRAL
    strength: SignalStrength = SignalStrength.MODERATE
    confidence: float = 0.0          # 0.0 → 1.0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArbitrageSignal(BaseSignal):
    symbol: str = ""
    buy_exchange: str = ""
    sell_exchange: str = ""
    buy_price: float = 0.0
    sell_price: float = 0.0
    spread_bps: float = 0.0
    gross_profit_bps: float = 0.0
    net_profit_bps: float = 0.0          # After fees + slippage
    estimated_fee_bps: float = 0.0
    estimated_slippage_bps: float = 0.0
    max_tradeable_size_usd: float = 0.0
    estimated_profit_usd: float = 0.0
    execution_window_ms: int = 0

    def __post_init__(self):
        self.signal_type = SignalType.ARBITRAGE


@dataclass
class SentimentSignal(BaseSignal):
    token: str = ""
    mention_count: int = 0
    mention_change_pct: float = 0.0
    sentiment_score: float = 0.0         # -1.0 (bearish) → +1.0 (bullish)
    bullish_pct: float = 0.0
    bearish_pct: float = 0.0
    neutral_pct: float = 0.0
    top_sources: List[str] = field(default_factory=list)
    trending_phrases: List[str] = field(default_factory=list)
    lookback_hours: int = 2

    def __post_init__(self):
        self.signal_type = SignalType.SENTIMENT


@dataclass
class LiquiditySignal(BaseSignal):
    symbol: str = ""
    exchange: str = ""
    wall_side: OrderSide = OrderSide.ASK
    wall_price: float = 0.0
    wall_size_usd: float = 0.0
    wall_size_base: float = 0.0
    imbalance_ratio: float = 0.5
    liquidity_gap_pct: float = 0.0
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0

    def __post_init__(self):
        self.signal_type = SignalType.LIQUIDITY


@dataclass
class WhaleSignal(BaseSignal):
    asset: str = ""
    from_address: str = ""
    to_address: str = ""
    amount: float = 0.0
    amount_usd: float = 0.0
    move_type: WhaleMoveType = WhaleMoveType.WALLET_TO_WALLET
    exchange_name: Optional[str] = None
    tx_hash: str = ""
    chain: str = "ethereum"
    historical_pattern: Optional[str] = None

    def __post_init__(self):
        self.signal_type = SignalType.WHALE


@dataclass
class AnomalySignal(BaseSignal):
    symbol: str = ""
    exchange: str = ""
    anomaly_type: str = ""
    observed_value: float = 0.0
    expected_value: float = 0.0
    z_score: float = 0.0
    description: str = ""

    def __post_init__(self):
        self.signal_type = SignalType.ANOMALY


# ─── Alert Model ──────────────────────────────────────────────────────────────

@dataclass
class Alert:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    signal_id: str = ""
    signal_type: SignalType = SignalType.ARBITRAGE
    channel: AlertChannel = AlertChannel.TELEGRAM
    title: str = ""
    body: str = ""
    priority: int = 1                    # 1=low, 5=critical
    sent_at: Optional[datetime] = None
    delivered: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


# ─── Pipeline Event ───────────────────────────────────────────────────────────

@dataclass
class PipelineEvent:
    """Internal event envelope for streaming pipeline."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = ""
    source: str = ""
    payload: Any = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    partition_key: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
