"""
Liquidity Intelligence Engine — Phase 4
Whale Tracking Engine — Phase 5

Liquidity: Analyzes order books for walls, imbalances, and gaps.
Whale: Tracks large on-chain transfers and exchange flows.
"""
import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.models import (
    LiquiditySignal, WhaleSignal, OrderBook, OrderBookLevel,
    OrderSide, SignalDirection, SignalStrength, WhaleMoveType
)
from config.settings import get_liquidity, get_whale
from utils.logging import get_logger

log = get_logger("signals.liquidity")
whale_log = get_logger("signals.whale")


# ═══════════════════════════════════════════════════════════════════════════════
# LIQUIDITY INTELLIGENCE ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class OrderBookStats:
    """Rolling statistics for order book analysis."""
    symbol: str
    exchange: str
    avg_level_size: float = 0.0
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0
    observation_count: int = 0
    level_size_history: deque = field(default_factory=lambda: deque(maxlen=50))

    def update(self, ob: OrderBook, mid_price: float) -> None:
        all_levels = ob.bids + ob.asks
        if not all_levels:
            return

        avg_size = sum(l.size for l in all_levels) / len(all_levels)
        self.level_size_history.append(avg_size)
        self.avg_level_size = sum(self.level_size_history) / len(self.level_size_history)
        self.bid_depth_usd = ob.bid_depth
        self.ask_depth_usd = ob.ask_depth
        self.observation_count += 1


class LiquidityEngine:
    """
    Real-time order book analysis engine.

    Detects:
    1. Buy/Sell walls — single levels with anomalously large size
    2. Imbalance — directional skew in order book depth
    3. Liquidity gaps — price gaps with thin or no orders

    Signal emission on detection of significant patterns.
    """

    SIGNAL_COOLDOWN_S = 120  # 2 min cooldown per symbol/exchange

    def __init__(self, on_signal: Optional[Callable] = None):
        self.config = get_liquidity()
        self._on_signal = on_signal
        self._ob_stats: Dict[str, OrderBookStats] = {}
        self._last_signal_ts: Dict[str, float] = {}
        self._signals_emitted = 0

    def _stats_key(self, symbol: str, exchange: str) -> str:
        return f"{exchange}:{symbol}"

    def _get_stats(self, ob: OrderBook) -> OrderBookStats:
        key = self._stats_key(ob.symbol, ob.exchange)
        if key not in self._ob_stats:
            self._ob_stats[key] = OrderBookStats(ob.symbol, ob.exchange)
        return self._ob_stats[key]

    async def on_orderbook(self, ob: OrderBook) -> None:
        """Process an order book snapshot."""
        if not ob.bids or not ob.asks:
            return

        mid = ob.mid_price
        if not mid:
            return

        stats = self._get_stats(ob)
        stats.update(ob, mid)

        # Need some history to detect anomalies
        if stats.observation_count < 5:
            return

        await self._detect_walls(ob, stats, mid)
        await self._detect_imbalance(ob, stats)

    async def _detect_walls(
        self, ob: OrderBook, stats: OrderBookStats, mid_price: float
    ) -> None:
        """Detect anomalously large orders that could act as price walls."""
        avg = stats.avg_level_size
        if avg == 0:
            return

        threshold_multiplier = self.config.wall_size_multiplier

        # Check asks (sell walls)
        for level in ob.asks[:self.config.depth_levels]:
            if level.size >= avg * threshold_multiplier:
                wall_usd = level.size * level.price
                distance_pct = ((level.price - mid_price) / mid_price) * 100
                if distance_pct < 5:  # Only walls within 5% of mid
                    await self._emit_wall_signal(
                        ob, level, OrderSide.ASK, wall_usd, stats
                    )
                    break

        # Check bids (buy walls)
        for level in ob.bids[:self.config.depth_levels]:
            if level.size >= avg * threshold_multiplier:
                wall_usd = level.size * level.price
                distance_pct = ((mid_price - level.price) / mid_price) * 100
                if distance_pct < 5:
                    await self._emit_wall_signal(
                        ob, level, OrderSide.BID, wall_usd, stats
                    )
                    break

    async def _detect_imbalance(
        self, ob: OrderBook, stats: OrderBookStats
    ) -> None:
        """Detect order book imbalance indicating directional pressure."""
        imbalance = ob.imbalance_ratio
        threshold = self.config.imbalance_threshold

        # Strong imbalance: bids >> asks (bullish) or asks >> bids (bearish)
        if imbalance > threshold:
            direction = SignalDirection.BULLISH
            wall_side = OrderSide.BID
        elif imbalance < (1 - threshold):
            direction = SignalDirection.BEARISH
            wall_side = OrderSide.ASK
        else:
            return

        cooldown_key = f"imbalance:{ob.exchange}:{ob.symbol}"
        now = time.time()
        if now - self._last_signal_ts.get(cooldown_key, 0) < self.SIGNAL_COOLDOWN_S:
            return

        self._last_signal_ts[cooldown_key] = now
        self._signals_emitted += 1

        confidence = self._imbalance_confidence(imbalance)

        signal = LiquiditySignal(
            symbol=ob.symbol,
            exchange=ob.exchange,
            wall_side=wall_side,
            wall_price=ob.best_bid if direction == SignalDirection.BULLISH else ob.best_ask,
            wall_size_usd=ob.bid_depth if direction == SignalDirection.BULLISH else ob.ask_depth,
            wall_size_base=0,
            imbalance_ratio=imbalance,
            bid_depth_usd=ob.bid_depth,
            ask_depth_usd=ob.ask_depth,
            direction=direction,
            strength=SignalStrength.STRONG if abs(imbalance - 0.5) > 0.3 else SignalStrength.MODERATE,
            confidence=confidence,
        )

        log.info(
            "Imbalance signal",
            symbol=ob.symbol, exchange=ob.exchange,
            imbalance=round(imbalance, 3), direction=direction.value
        )

        if self._on_signal:
            await self._on_signal(signal)

    async def _emit_wall_signal(
        self, ob: OrderBook, level: OrderBookLevel,
        side: OrderSide, wall_usd: float, stats: OrderBookStats
    ) -> None:
        cooldown_key = f"wall:{ob.exchange}:{ob.symbol}:{side.value}"
        now = time.time()
        if now - self._last_signal_ts.get(cooldown_key, 0) < self.SIGNAL_COOLDOWN_S:
            return

        self._last_signal_ts[cooldown_key] = now
        self._signals_emitted += 1

        direction = SignalDirection.BEARISH if side == OrderSide.ASK else SignalDirection.BULLISH
        confidence = min(level.size / (stats.avg_level_size * 20), 1.0)

        signal = LiquiditySignal(
            symbol=ob.symbol,
            exchange=ob.exchange,
            wall_side=side,
            wall_price=level.price,
            wall_size_usd=wall_usd,
            wall_size_base=level.size,
            imbalance_ratio=ob.imbalance_ratio,
            bid_depth_usd=ob.bid_depth,
            ask_depth_usd=ob.ask_depth,
            direction=direction,
            strength=SignalStrength.STRONG,
            confidence=round(confidence, 3),
        )

        log.info(
            "Wall signal",
            symbol=ob.symbol, exchange=ob.exchange,
            side=side.value, price=level.price,
            wall_usd=round(wall_usd, 0),
        )

        if self._on_signal:
            await self._on_signal(signal)

    def _imbalance_confidence(self, imbalance: float) -> float:
        deviation = abs(imbalance - 0.5) * 2  # 0 → 1
        return round(min(deviation * 1.2, 1.0), 3)


# ═══════════════════════════════════════════════════════════════════════════════
# WHALE TRACKING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

# Known exchange hot wallet addresses (illustrative subset)
KNOWN_EXCHANGE_WALLETS = {
    # Binance
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance",
    # Coinbase
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": "Coinbase",
    # Kraken
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2": "Kraken",
    # OKX
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX",
}


@dataclass
class WhaleTx:
    """Normalized on-chain transaction."""
    chain: str
    tx_hash: str
    from_address: str
    to_address: str
    asset: str
    amount: float
    amount_usd: float
    block_number: int
    timestamp: datetime


class WhaleEngine:
    """
    On-chain whale activity detection engine.

    Monitors large transfers on Ethereum and Bitcoin networks.
    Classifies move type (exchange deposit/withdrawal/wallet transfer).
    Correlates with historical patterns for predictive context.
    """

    SIGNAL_COOLDOWN_S = 60

    def __init__(self, on_signal: Optional[Callable] = None):
        self.config = get_whale()
        self._on_signal = on_signal
        self._last_signal_ts: Dict[str, float] = {}
        self._recent_txs: deque = deque(maxlen=500)
        self._wallet_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=20)
        )
        self._signals_emitted = 0

    async def on_transaction(self, tx: WhaleTx) -> None:
        """Process a normalized on-chain transaction."""
        # Check minimum thresholds
        if tx.amount_usd < self.config.min_usd_value:
            return

        self._recent_txs.append(tx)
        self._wallet_history[tx.from_address].append(tx)

        # Classify the move
        move_type = self._classify_move(tx)

        # Historical pattern analysis
        pattern = self._analyze_pattern(tx, move_type)

        # Confidence scoring
        confidence = self._compute_confidence(tx, move_type, pattern)

        # Determine signal direction
        direction = self._assess_direction(move_type, pattern)

        # Strength based on USD size
        strength = self._classify_strength(tx.amount_usd)

        cooldown_key = f"{tx.tx_hash}"
        now = time.time()
        if now - self._last_signal_ts.get(cooldown_key, 0) < self.SIGNAL_COOLDOWN_S:
            return

        self._last_signal_ts[cooldown_key] = now
        self._signals_emitted += 1

        exchange = KNOWN_EXCHANGE_WALLETS.get(
            tx.to_address.lower()
        ) or KNOWN_EXCHANGE_WALLETS.get(tx.from_address.lower())

        signal = WhaleSignal(
            asset=tx.asset,
            from_address=tx.from_address,
            to_address=tx.to_address,
            amount=tx.amount,
            amount_usd=tx.amount_usd,
            move_type=move_type,
            exchange_name=exchange,
            tx_hash=tx.tx_hash,
            chain=tx.chain,
            historical_pattern=pattern,
            direction=direction,
            strength=strength,
            confidence=confidence,
        )

        whale_log.info(
            "Whale signal",
            asset=tx.asset, amount_usd=round(tx.amount_usd, 0),
            move_type=move_type.value, exchange=exchange,
            tx_hash=tx.tx_hash[:16],
        )

        if self._on_signal:
            await self._on_signal(signal)

    def _classify_move(self, tx: WhaleTx) -> WhaleMoveType:
        to_lower = tx.to_address.lower()
        from_lower = tx.from_address.lower()

        if to_lower in KNOWN_EXCHANGE_WALLETS:
            return WhaleMoveType.EXCHANGE_DEPOSIT
        elif from_lower in KNOWN_EXCHANGE_WALLETS:
            return WhaleMoveType.EXCHANGE_WITHDRAWAL
        else:
            return WhaleMoveType.WALLET_TO_WALLET

    def _analyze_pattern(self, tx: WhaleTx, move_type: WhaleMoveType) -> Optional[str]:
        """Check if address has a history of moves preceding price action."""
        history = self._wallet_history.get(tx.from_address, deque())
        if len(history) < 3:
            return None

        deposit_count = sum(
            1 for h in history
            if self._classify_move(h) == WhaleMoveType.EXCHANGE_DEPOSIT
        )

        if move_type == WhaleMoveType.EXCHANGE_DEPOSIT:
            if deposit_count >= 2:
                return "Repeat depositor — historically precedes sell pressure"

        withdrawal_count = sum(
            1 for h in history
            if self._classify_move(h) == WhaleMoveType.EXCHANGE_WITHDRAWAL
        )
        if move_type == WhaleMoveType.EXCHANGE_WITHDRAWAL and withdrawal_count >= 2:
            return "Repeat withdrawer — historically precedes accumulation"

        return None

    def _compute_confidence(
        self, tx: WhaleTx, move_type: WhaleMoveType, pattern: Optional[str]
    ) -> float:
        score = 0.0
        # Size factor (40%)
        size_factor = min(tx.amount_usd / (self.config.min_usd_value * 10), 1.0)
        score += 0.40 * size_factor
        # Known exchange classification (30%)
        if move_type in (WhaleMoveType.EXCHANGE_DEPOSIT, WhaleMoveType.EXCHANGE_WITHDRAWAL):
            score += 0.30
        else:
            score += 0.10
        # Historical pattern (30%)
        score += 0.30 if pattern else 0.10
        return round(score, 3)

    def _assess_direction(
        self, move_type: WhaleMoveType, pattern: Optional[str]
    ) -> SignalDirection:
        if move_type == WhaleMoveType.EXCHANGE_DEPOSIT:
            return SignalDirection.BEARISH
        elif move_type == WhaleMoveType.EXCHANGE_WITHDRAWAL:
            return SignalDirection.BULLISH
        return SignalDirection.NEUTRAL

    def _classify_strength(self, amount_usd: float) -> SignalStrength:
        if amount_usd >= 50_000_000:
            return SignalStrength.CRITICAL
        elif amount_usd >= 10_000_000:
            return SignalStrength.STRONG
        elif amount_usd >= 3_000_000:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK
