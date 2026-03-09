"""
Arbitrage Signal Engine — Phase 1 MVP Core

Detects profitable cross-exchange price discrepancies in real-time.

Pipeline:
  Live price ticks → Spread calculation → Fee+Slippage estimation
  → Profitability filter → Signal emission → Alert dispatch

Key design decisions:
- Per-symbol price registry with sub-millisecond lookups
- Vectorized fee/slippage model per exchange pair
- Confidence scoring via liquidity depth + historical spread volatility
- Signal deduplication with cooldown window
"""
import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import combinations
from typing import Callable, Dict, List, Optional, Tuple
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.models import (
    ArbitrageSignal, PriceTick, SignalDirection, SignalStrength
)
from config.settings import get_arbitrage, get_exchanges, ArbitrageConfig
from utils.logging import get_logger

log = get_logger("signals.arbitrage")


@dataclass
class SpreadSnapshot:
    """Point-in-time spread between two exchanges for a symbol."""
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float    # ask on buy side
    sell_price: float   # bid on sell side
    gross_spread_bps: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class ExchangeFeeModel:
    """
    Fee and slippage model for a specific exchange.
    Slippage is estimated based on order size relative to available liquidity.
    """
    exchange: str
    taker_fee_bps: float
    base_slippage_bps: float = 2.0       # Minimum slippage assumption
    size_impact_coefficient: float = 0.1  # bps per $10k notional

    def estimate_cost_bps(self, notional_usd: float = 10_000) -> float:
        """Total execution cost in bps for given notional."""
        slippage = self.base_slippage_bps + (
            notional_usd / 10_000 * self.size_impact_coefficient
        )
        return self.taker_fee_bps + slippage


class SpreadHistory:
    """
    Rolling window of spread observations for a symbol-pair.
    Used to compute spread volatility and mean-reversion probability.
    """
    WINDOW_SIZE = 200

    def __init__(self):
        self._spreads: deque = deque(maxlen=self.WINDOW_SIZE)

    def add(self, spread_bps: float) -> None:
        self._spreads.append(spread_bps)

    @property
    def mean(self) -> float:
        if not self._spreads:
            return 0.0
        return sum(self._spreads) / len(self._spreads)

    @property
    def std(self) -> float:
        if len(self._spreads) < 2:
            return 0.0
        m = self.mean
        variance = sum((x - m) ** 2 for x in self._spreads) / (len(self._spreads) - 1)
        return math.sqrt(variance)

    @property
    def z_score(self) -> float:
        """Z-score of latest observation vs historical distribution."""
        if len(self._spreads) < 10 or self.std == 0:
            return 0.0
        latest = self._spreads[-1]
        return (latest - self.mean) / self.std

    def __len__(self) -> int:
        return len(self._spreads)


class ArbitrageEngine:
    """
    Core arbitrage detection engine.

    Maintains a per-symbol, per-exchange price registry.
    On each tick update, evaluates all exchange pairs for profitable spreads.
    Emits ArbitrageSignal objects to registered callbacks.
    """

    # Signal cooldown: don't re-emit same pair within this window
    SIGNAL_COOLDOWN_S = 30.0
    # Stale price threshold: ignore prices older than this
    PRICE_STALE_THRESHOLD_S = 5.0

    def __init__(
        self,
        config: Optional[ArbitrageConfig] = None,
        on_signal: Optional[Callable] = None,
    ):
        self.config = config or get_arbitrage()
        self._on_signal = on_signal

        # {symbol: {exchange: PriceTick}}
        self._price_registry: Dict[str, Dict[str, PriceTick]] = defaultdict(dict)

        # Fee models per exchange
        exchanges_cfg = get_exchanges()
        self._fee_models: Dict[str, ExchangeFeeModel] = {
            name: ExchangeFeeModel(
                exchange=name,
                taker_fee_bps=cfg.fee_taker * 10_000,
            )
            for name, cfg in exchanges_cfg.items()
        }

        # Spread history per (symbol, exchange_a, exchange_b)
        self._spread_history: Dict[Tuple, SpreadHistory] = defaultdict(SpreadHistory)

        # Signal cooldown registry: key → last_emitted_ts
        self._last_signal_ts: Dict[str, float] = {}

        # Performance counters
        self._ticks_processed = 0
        self._signals_emitted = 0
        self._last_scan_duration_us = 0.0

        log.info(
            "ArbitrageEngine initialized",
            min_profit_bps=self.config.min_profit_bps,
            min_confidence=self.config.min_confidence,
            symbols=self.config.symbols,
        )

    # ── Public Interface ──────────────────────────────────────────────────────

    async def on_tick(self, tick: PriceTick) -> None:
        """
        Ingest a price tick and trigger spread evaluation.
        Called by exchange connectors on every market update.
        """
        if tick.symbol not in self.config.symbols:
            return
        if tick.bid <= 0 or tick.ask <= 0:
            return

        self._price_registry[tick.symbol][tick.exchange] = tick
        self._ticks_processed += 1

        # Only scan when we have prices from ≥2 exchanges
        exchange_prices = self._price_registry[tick.symbol]
        if len(exchange_prices) < 2:
            return

        await self._scan_spreads(tick.symbol, exchange_prices)

    async def _scan_spreads(
        self,
        symbol: str,
        exchange_prices: Dict[str, PriceTick],
    ) -> None:
        """
        Evaluate all exchange pairs for this symbol.
        Time complexity: O(n²) where n = number of exchanges (max ~5).
        """
        t_start = time.perf_counter_ns()

        now = time.time()
        exchanges = list(exchange_prices.keys())

        for buy_ex, sell_ex in combinations(exchanges, 2):
            # Evaluate both directions: buy A/sell B and buy B/sell A
            for b_ex, s_ex in [(buy_ex, sell_ex), (sell_ex, buy_ex)]:
                tick_b = exchange_prices[b_ex]  # We BUY here (pay ask)
                tick_s = exchange_prices[s_ex]  # We SELL here (receive bid)

                # Stale price check
                if (now - tick_b.timestamp.timestamp() > self.PRICE_STALE_THRESHOLD_S or
                        now - tick_s.timestamp.timestamp() > self.PRICE_STALE_THRESHOLD_S):
                    continue

                signal = self._evaluate_spread(symbol, tick_b, tick_s)
                if signal:
                    await self._emit_signal(signal)

        self._last_scan_duration_us = (time.perf_counter_ns() - t_start) / 1000

    def _evaluate_spread(
        self,
        symbol: str,
        buy_tick: PriceTick,
        sell_tick: PriceTick,
    ) -> Optional[ArbitrageSignal]:
        """
        Core profitability evaluation.
        Returns ArbitrageSignal if opportunity meets thresholds, else None.
        """
        # Buy at ask, sell at bid
        buy_price = buy_tick.ask
        sell_price = sell_tick.bid

        if buy_price >= sell_price:
            return None  # No spread

        gross_spread_bps = ((sell_price - buy_price) / buy_price) * 10_000

        # Fee estimation
        buy_fee_model = self._fee_models.get(
            buy_tick.exchange,
            ExchangeFeeModel(buy_tick.exchange, 10.0)
        )
        sell_fee_model = self._fee_models.get(
            sell_tick.exchange,
            ExchangeFeeModel(sell_tick.exchange, 10.0)
        )

        # Assume $10,000 notional for initial scan
        notional = 10_000.0
        total_cost_bps = (
            buy_fee_model.estimate_cost_bps(notional) +
            sell_fee_model.estimate_cost_bps(notional)
        )

        net_profit_bps = gross_spread_bps - total_cost_bps

        if net_profit_bps < self.config.min_profit_bps:
            return None

        # Track spread history for this pair
        history_key = (symbol, buy_tick.exchange, sell_tick.exchange)
        history = self._spread_history[history_key]
        history.add(gross_spread_bps)

        # Confidence scoring
        confidence = self._compute_confidence(
            gross_spread_bps=gross_spread_bps,
            net_profit_bps=net_profit_bps,
            history=history,
            buy_tick=buy_tick,
            sell_tick=sell_tick,
        )

        if confidence < self.config.min_confidence:
            return None

        # Signal strength classification
        strength = self._classify_strength(net_profit_bps)

        # Max tradeable size estimation (simple liquidity heuristic)
        max_size_usd = min(
            buy_tick.volume_24h * buy_tick.ask * 0.001,  # 0.1% of daily vol
            100_000.0
        ) or 10_000.0

        return ArbitrageSignal(
            symbol=symbol,
            buy_exchange=buy_tick.exchange,
            sell_exchange=sell_tick.exchange,
            buy_price=buy_price,
            sell_price=sell_price,
            spread_bps=gross_spread_bps,
            gross_profit_bps=gross_spread_bps,
            net_profit_bps=net_profit_bps,
            estimated_fee_bps=total_cost_bps,
            estimated_slippage_bps=buy_fee_model.base_slippage_bps + sell_fee_model.base_slippage_bps,
            max_tradeable_size_usd=max_size_usd,
            estimated_profit_usd=(net_profit_bps / 10_000) * max_size_usd,
            execution_window_ms=self.config.max_execution_time_ms,
            confidence=confidence,
            direction=SignalDirection.BULLISH,
            strength=strength,
        )

    def _compute_confidence(
        self,
        gross_spread_bps: float,
        net_profit_bps: float,
        history: SpreadHistory,
        buy_tick: PriceTick,
        sell_tick: PriceTick,
    ) -> float:
        """
        Multi-factor confidence scoring (0.0 → 1.0).

        Factors:
        1. Profit margin vs minimum threshold (higher = more confident)
        2. Spread persistence (based on historical z-score)
        3. Price freshness (penalty for stale prices)
        4. Bid/ask spread quality on both legs
        """
        score = 0.0

        # Factor 1: Profit margin (40% weight)
        profit_ratio = min(net_profit_bps / (self.config.min_profit_bps * 3), 1.0)
        score += 0.40 * profit_ratio

        # Factor 2: Historical persistence (30% weight)
        if len(history) >= 20:
            z = history.z_score
            if z > 1.5:
                persistence = min(z / 3.0, 1.0)
            else:
                persistence = 0.5
        else:
            persistence = 0.5  # neutral when insufficient history
        score += 0.30 * persistence

        # Factor 3: Price freshness (20% weight)
        now = time.time()
        age_b = now - buy_tick.timestamp.timestamp()
        age_s = now - sell_tick.timestamp.timestamp()
        max_age = self.PRICE_STALE_THRESHOLD_S
        freshness = 1.0 - min(max(age_b, age_s) / max_age, 1.0)
        score += 0.20 * freshness

        # Factor 4: Tight spreads on source exchanges (10% weight)
        buy_spread_quality = 1.0 - min(buy_tick.spread_bps / 10.0, 1.0)
        sell_spread_quality = 1.0 - min(sell_tick.spread_bps / 10.0, 1.0)
        score += 0.10 * ((buy_spread_quality + sell_spread_quality) / 2)

        return round(score, 4)

    def _classify_strength(self, net_profit_bps: float) -> SignalStrength:
        if net_profit_bps >= 100:
            return SignalStrength.CRITICAL
        elif net_profit_bps >= 50:
            return SignalStrength.STRONG
        elif net_profit_bps >= 30:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK

    async def _emit_signal(self, signal: ArbitrageSignal) -> None:
        """Deduplicate and emit signal."""
        cooldown_key = f"{signal.symbol}:{signal.buy_exchange}:{signal.sell_exchange}"
        now = time.time()

        last_emitted = self._last_signal_ts.get(cooldown_key, 0)
        if now - last_emitted < self.SIGNAL_COOLDOWN_S:
            return

        self._last_signal_ts[cooldown_key] = now
        self._signals_emitted += 1

        log.info(
            "Arbitrage signal emitted",
            symbol=signal.symbol,
            buy_exchange=signal.buy_exchange,
            sell_exchange=signal.sell_exchange,
            net_profit_bps=round(signal.net_profit_bps, 2),
            confidence=signal.confidence,
            strength=signal.strength.value,
        )

        if self._on_signal:
            await self._on_signal(signal)

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        return {
            "ticks_processed": self._ticks_processed,
            "signals_emitted": self._signals_emitted,
            "last_scan_duration_us": self._last_scan_duration_us,
            "tracked_symbols": list(self._price_registry.keys()),
            "price_coverage": {
                sym: list(exs.keys())
                for sym, exs in self._price_registry.items()
            },
        }

    def get_current_spreads(self) -> List[SpreadSnapshot]:
        """Return all current observable spreads (for monitoring)."""
        snapshots = []
        for symbol, exchange_prices in self._price_registry.items():
            exchanges = list(exchange_prices.items())
            for (ex_a, tick_a), (ex_b, tick_b) in combinations(exchanges, 2):
                # Buy on A, sell on B
                if tick_a.ask < tick_b.bid:
                    bps = ((tick_b.bid - tick_a.ask) / tick_a.ask) * 10_000
                    snapshots.append(SpreadSnapshot(
                        symbol=symbol, buy_exchange=ex_a, sell_exchange=ex_b,
                        buy_price=tick_a.ask, sell_price=tick_b.bid,
                        gross_spread_bps=bps,
                    ))
        return sorted(snapshots, key=lambda x: x.gross_spread_bps, reverse=True)
