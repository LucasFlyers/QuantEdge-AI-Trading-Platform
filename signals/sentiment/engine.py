"""
Sentiment Intelligence Engine — Phase 3

Processes social data from multiple sources:
- X (Twitter) API v2
- Reddit PushShift / PRAW
- Telegram public channels

Pipeline: Social Data → Cleaning → Sentiment Model → Token Tracking → Signal
"""
import asyncio
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Set, Tuple
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.models import SentimentSignal, SignalDirection, SignalStrength
from config.settings import get_sentiment
from utils.logging import get_logger

log = get_logger("signals.sentiment")


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class SocialPost:
    source: str          # twitter, reddit, telegram
    text: str
    author: str
    timestamp: datetime
    engagement: int = 0  # likes + retweets + upvotes
    post_id: str = ""


@dataclass
class TokenMentionWindow:
    """Sliding window of mentions for a token."""
    token: str
    window_minutes: int

    def __post_init__(self):
        self._mentions: deque = deque()
        self._sentiment_scores: deque = deque()

    def add(self, timestamp: datetime, sentiment: float, engagement: int = 0):
        cutoff = datetime.utcnow() - timedelta(minutes=self.window_minutes)
        self._mentions.append((timestamp, engagement))
        self._sentiment_scores.append(sentiment)
        # Prune old entries
        while self._mentions and self._mentions[0][0] < cutoff:
            self._mentions.popleft()
            self._sentiment_scores.popleft()

    @property
    def count(self) -> int:
        return len(self._mentions)

    @property
    def weighted_sentiment(self) -> float:
        if not self._mentions:
            return 0.0
        total_weight = sum(max(eng, 1) for _, eng in self._mentions)
        weighted_sum = sum(
            s * max(eng, 1)
            for s, (_, eng) in zip(self._sentiment_scores, self._mentions)
        )
        return weighted_sum / total_weight if total_weight else 0.0

    @property
    def raw_sentiment(self) -> float:
        if not self._sentiment_scores:
            return 0.0
        return sum(self._sentiment_scores) / len(self._sentiment_scores)


# ─── Text Processing ──────────────────────────────────────────────────────────

class TextPreprocessor:
    """Clean and normalize social media text for sentiment analysis."""

    # Regex patterns
    URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")
    MENTION_PATTERN = re.compile(r"@\w+")
    HASHTAG_PATTERN = re.compile(r"#(\w+)")
    CASHTAG_PATTERN = re.compile(r"\$([A-Z]{2,10})\b")
    EMOJI_PATTERN = re.compile(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
        r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
        r"\u2600-\u26FF\u2700-\u27BF]+"
    )
    REPEAT_CHAR = re.compile(r"(.)\1{3,}")

    def clean(self, text: str) -> str:
        """Remove noise while preserving semantic content."""
        text = self.URL_PATTERN.sub(" ", text)
        text = self.MENTION_PATTERN.sub(" ", text)
        text = self.HASHTAG_PATTERN.sub(r" \1 ", text)  # keep hashtag words
        text = self.EMOJI_PATTERN.sub(" ", text)
        text = self.REPEAT_CHAR.sub(r"\1\1", text)      # normalize repetition
        text = re.sub(r"\s+", " ", text).strip()
        return text[:512]  # cap length

    def extract_tokens(self, text: str) -> Set[str]:
        """Extract crypto token mentions ($BTC, $ETH etc.)"""
        cashtags = set(self.CASHTAG_PATTERN.findall(text.upper()))
        # Also catch common patterns without $ (BTC, ETH)
        words = text.upper().split()
        known_tokens = {
            "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX",
            "MATIC", "DOT", "LINK", "UNI", "AAVE", "RNDR", "INJ",
            "ARB", "OP", "APT", "SUI", "TIA", "JUP", "PYTH"
        }
        word_mentions = {w for w in words if w in known_tokens}
        return cashtags | word_mentions


# ─── Lightweight Sentiment Classifier ────────────────────────────────────────

class LexiconSentimentClassifier:
    """
    Fast lexicon-based sentiment classifier.
    Use as fallback when transformer model is unavailable.
    For production, replace with transformers pipeline (HuggingFace).
    """

    BULLISH_TERMS = {
        # Strong bullish
        "moon", "mooning", "bullish", "pump", "breakout", "surge", "rally",
        "ATH", "alltime", "buy", "accumulate", "hodl", "hold", "diamond",
        "rip", "skyrocket", "explode", "launch", "rocket", "🚀",
        # Medium bullish
        "up", "rise", "green", "gains", "profit", "win", "positive",
        "strong", "support", "floor", "bounce", "recover", "rebound",
    }

    BEARISH_TERMS = {
        # Strong bearish
        "dump", "crash", "bearish", "sell", "short", "rekt", "down",
        "rug", "scam", "dead", "finished", "collapse", "spiral", "bleed",
        "capitulate", "liquidate", "panic",
        # Medium bearish
        "drop", "fall", "red", "loss", "weak", "resistance", "breakdown",
        "reject", "failed", "below", "support broken",
    }

    def classify(self, text: str) -> Tuple[float, float, float]:
        """
        Returns (bullish_pct, neutral_pct, bearish_pct) summing to 1.0.
        And a sentiment score from -1.0 to +1.0.
        """
        text_lower = text.lower()
        words = set(text_lower.split())

        bull_hits = len(words & {t.lower() for t in self.BULLISH_TERMS})
        bear_hits = len(words & {t.lower() for t in self.BEARISH_TERMS})
        total_hits = bull_hits + bear_hits

        if total_hits == 0:
            return 0.0, 1.0, 0.0  # neutral

        bull_pct = bull_hits / (total_hits + 2)  # smoothed
        bear_pct = bear_hits / (total_hits + 2)
        neutral_pct = 1.0 - bull_pct - bear_pct

        score = bull_pct - bear_pct
        return bull_pct, neutral_pct, bear_pct


# ─── Sentiment Engine ─────────────────────────────────────────────────────────

class SentimentEngine:
    """
    Real-time social sentiment analysis engine.

    Tracks mention velocity and sentiment shifts per token.
    Emits signals when anomalous patterns are detected.
    """

    def __init__(self, on_signal: Optional[Callable] = None):
        self.config = get_sentiment()
        self._on_signal = on_signal
        self._preprocessor = TextPreprocessor()
        self._classifier = LexiconSentimentClassifier()

        # Per-token mention windows
        self._mention_windows: Dict[str, TokenMentionWindow] = {}

        # Baseline mention rates (updated hourly)
        self._baseline_mention_rates: Dict[str, float] = {}
        self._baseline_window: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=60)  # 60 x 1-min samples = 1 hour
        )

        # Signal deduplication
        self._last_signal_ts: Dict[str, float] = {}
        self.SIGNAL_COOLDOWN_S = 300  # 5 min cooldown per token

        # Stats
        self._posts_processed = 0
        self._signals_emitted = 0

    def _get_mention_window(self, token: str) -> TokenMentionWindow:
        if token not in self._mention_windows:
            self._mention_windows[token] = TokenMentionWindow(
                token=token,
                window_minutes=self.config.lookback_window_minutes,
            )
        return self._mention_windows[token]

    async def process_post(self, post: SocialPost) -> None:
        """
        Ingest a single social post. Extracts tokens, runs sentiment,
        updates windows, checks for signal conditions.
        """
        self._posts_processed += 1

        # Clean text
        clean_text = self._preprocessor.clean(post.text)
        if len(clean_text) < 10:
            return

        # Extract token mentions
        tokens = self._preprocessor.extract_tokens(post.text)
        if not tokens:
            return

        # Classify sentiment
        bull_pct, neutral_pct, bear_pct = self._classifier.classify(clean_text)
        sentiment_score = bull_pct - bear_pct

        # Update each mentioned token's window
        for token in tokens:
            window = self._get_mention_window(token)
            window.add(post.timestamp, sentiment_score, post.engagement)

            # Check if this warrants a signal
            await self._evaluate_signal(token, window, bull_pct, neutral_pct, bear_pct)

    async def _evaluate_signal(
        self,
        token: str,
        window: TokenMentionWindow,
        bull_pct: float,
        neutral_pct: float,
        bear_pct: float,
    ) -> None:
        """Evaluate whether current state triggers a sentiment signal."""
        # Need minimum mentions
        if window.count < self.config.min_mentions_threshold:
            return

        # Cooldown check
        now = time.time()
        last_ts = self._last_signal_ts.get(token, 0)
        if now - last_ts < self.SIGNAL_COOLDOWN_S:
            return

        # Get baseline for comparison
        baseline = self._baseline_mention_rates.get(token)
        if baseline is None or baseline == 0:
            self._baseline_mention_rates[token] = window.count
            return

        # Check for mention surge
        mention_change_pct = ((window.count - baseline) / baseline) * 100

        if abs(mention_change_pct) < (self.config.surge_multiplier - 1) * 100:
            return  # Not enough surge

        # Determine direction
        sentiment_score = window.weighted_sentiment
        if sentiment_score > 0.1:
            direction = SignalDirection.BULLISH
        elif sentiment_score < -0.1:
            direction = SignalDirection.BEARISH
        else:
            direction = SignalDirection.NEUTRAL

        # Confidence scoring
        confidence = self._compute_confidence(
            window=window,
            mention_change_pct=mention_change_pct,
            sentiment_score=sentiment_score,
        )

        if confidence < self.config.confidence_threshold:
            return

        # Emit signal
        self._last_signal_ts[token] = now
        self._signals_emitted += 1

        strength = self._classify_strength(mention_change_pct, confidence)

        signal = SentimentSignal(
            token=token,
            mention_count=window.count,
            mention_change_pct=mention_change_pct,
            sentiment_score=sentiment_score,
            bullish_pct=bull_pct * 100,
            bearish_pct=bear_pct * 100,
            neutral_pct=neutral_pct * 100,
            lookback_hours=self.config.lookback_window_minutes // 60,
            direction=direction,
            strength=strength,
            confidence=confidence,
        )

        log.info(
            "Sentiment signal emitted",
            token=token,
            mention_count=window.count,
            mention_change_pct=round(mention_change_pct, 1),
            sentiment_score=round(sentiment_score, 3),
            direction=direction.value,
            confidence=confidence,
        )

        if self._on_signal:
            await self._on_signal(signal)

    def _compute_confidence(
        self,
        window: TokenMentionWindow,
        mention_change_pct: float,
        sentiment_score: float,
    ) -> float:
        score = 0.0

        # Mention volume surge (40%)
        surge_magnitude = min(abs(mention_change_pct) / 500.0, 1.0)
        score += 0.40 * surge_magnitude

        # Sentiment clarity (30%) — how non-neutral is it
        sentiment_clarity = min(abs(sentiment_score) * 2, 1.0)
        score += 0.30 * sentiment_clarity

        # Sample size confidence (20%)
        sample_confidence = min(window.count / 100.0, 1.0)
        score += 0.20 * sample_confidence

        # Alignment: surge direction matches sentiment direction (10%)
        if (mention_change_pct > 0 and sentiment_score > 0) or \
           (mention_change_pct < 0 and sentiment_score < 0):
            score += 0.10

        return round(score, 4)

    def _classify_strength(self, mention_change_pct: float, confidence: float) -> SignalStrength:
        if abs(mention_change_pct) > 300 and confidence > 0.75:
            return SignalStrength.CRITICAL
        elif abs(mention_change_pct) > 150 and confidence > 0.65:
            return SignalStrength.STRONG
        elif abs(mention_change_pct) > 80:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK

    def update_baseline(self, token: str) -> None:
        """Update rolling baseline for mention rate normalization."""
        if token in self._mention_windows:
            current = self._mention_windows[token].count
            self._baseline_window[token].append(current)
            if self._baseline_window[token]:
                self._baseline_mention_rates[token] = (
                    sum(self._baseline_window[token]) / len(self._baseline_window[token])
                )
