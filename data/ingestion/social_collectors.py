"""
Social Data Collectors — Phase 2

Sources (all async, non-blocking):
  - Reddit JSON API     — no auth required, public subreddits
  - CryptoPanic API     — free tier with CRYPTOPANIC_API_KEY (optional)
  - Fear & Greed Index  — no auth, used as market sentiment baseline

Each collector yields SocialPost objects to the SentimentEngine.
"""
import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncGenerator, Callable, List, Optional

import aiohttp

from utils.logging import get_logger

log = get_logger("ingestion.social")


# ─── Shared ───────────────────────────────────────────────────────────────────

@dataclass
class SocialPost:
    source: str
    text: str
    author: str
    timestamp: datetime
    engagement: int = 0
    post_id: str = ""


# ─── Reddit Collector ─────────────────────────────────────────────────────────

CRYPTO_SUBREDDITS = [
    "CryptoCurrency",
    "Bitcoin",
    "ethereum",
    "solana",
    "CryptoMarkets",
    "altcoin",
    "SatoshiStreetBets",
]

REDDIT_HEADERS = {
    "User-Agent": "QuantEdge/1.0 (trading intelligence platform)",
}


class RedditCollector:
    """
    Polls Reddit's public JSON API — no OAuth needed.
    Checks each subreddit for new posts every `interval_s` seconds.
    """

    def __init__(
        self,
        subreddits: List[str] = CRYPTO_SUBREDDITS,
        interval_s: int = 60,
        on_post: Optional[Callable] = None,
    ):
        self.subreddits = subreddits
        self.interval_s = interval_s
        self._on_post = on_post
        self._seen_ids: set = set()
        self._running = False
        self._posts_collected = 0

    async def start(self, session: aiohttp.ClientSession) -> None:
        self._running = True
        log.info("Reddit collector started", subreddits=self.subreddits)

        while self._running:
            for subreddit in self.subreddits:
                try:
                    await self._poll_subreddit(session, subreddit)
                except Exception as e:
                    log.warning("Reddit poll failed", subreddit=subreddit, error=str(e))
                await asyncio.sleep(1)  # polite rate limit between subs

            await asyncio.sleep(self.interval_s)

    async def stop(self):
        self._running = False

    async def _poll_subreddit(
        self, session: aiohttp.ClientSession, subreddit: str
    ) -> None:
        url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=25"
        async with session.get(url, headers=REDDIT_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return
            data = await resp.json()

        posts = data.get("data", {}).get("children", [])
        new_count = 0

        for item in posts:
            post_data = item.get("data", {})
            post_id = post_data.get("id", "")

            if post_id in self._seen_ids:
                continue
            self._seen_ids.add(post_id)

            # Combine title + selftext for richer signal
            title = post_data.get("title", "")
            body = post_data.get("selftext", "")[:300]
            text = f"{title}. {body}".strip()

            if len(text) < 10:
                continue

            engagement = (
                post_data.get("score", 0)
                + post_data.get("num_comments", 0) * 2
            )

            post = SocialPost(
                source=f"reddit/r/{subreddit}",
                text=text,
                author=post_data.get("author", "unknown"),
                timestamp=datetime.utcfromtimestamp(
                    post_data.get("created_utc", time.time())
                ),
                engagement=engagement,
                post_id=post_id,
            )

            self._posts_collected += 1
            new_count += 1

            if self._on_post:
                await self._on_post(post)

        # Trim seen_ids so it doesn't grow forever
        if len(self._seen_ids) > 5000:
            self._seen_ids = set(list(self._seen_ids)[-2000:])

        if new_count > 0:
            log.info("Reddit posts collected", subreddit=subreddit, new=new_count)


# ─── CryptoPanic Collector ────────────────────────────────────────────────────

class CryptoPanicCollector:
    """
    Polls CryptoPanic for crypto news headlines.
    Works without an API key (public=true), better with one.
    Set CRYPTOPANIC_API_KEY for higher rate limits and more data.
    """

    BASE_URL = "https://cryptopanic.com/api/v1/posts/"

    def __init__(
        self,
        interval_s: int = 120,
        on_post: Optional[Callable] = None,
    ):
        self.interval_s = interval_s
        self._on_post = on_post
        self._api_key = os.getenv("CRYPTOPANIC_API_KEY", "")
        self._seen_ids: set = set()
        self._running = False
        self._posts_collected = 0

    async def start(self, session: aiohttp.ClientSession) -> None:
        self._running = True
        configured = "with API key" if self._api_key else "public mode"
        log.info("CryptoPanic collector started", mode=configured)

        while self._running:
            try:
                await self._poll(session)
            except Exception as e:
                log.warning("CryptoPanic poll failed", error=str(e))
            await asyncio.sleep(self.interval_s)

    async def stop(self):
        self._running = False

    async def _poll(self, session: aiohttp.ClientSession) -> None:
        params = {"public": "true", "kind": "news"}
        if self._api_key:
            params["auth_token"] = self._api_key

        async with session.get(
            self.BASE_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return
            data = await resp.json()

        results = data.get("results", [])
        new_count = 0

        for item in results:
            post_id = str(item.get("id", ""))
            if post_id in self._seen_ids:
                continue
            self._seen_ids.add(post_id)

            title = item.get("title", "")
            if len(title) < 5:
                continue

            # Extract currencies mentioned
            currencies = [c.get("code", "") for c in item.get("currencies", [])]
            text = title
            if currencies:
                text = f"{title} ${' $'.join(currencies)}"

            # CryptoPanic votes as engagement
            votes = item.get("votes", {})
            engagement = (
                votes.get("positive", 0)
                + votes.get("liked", 0) * 2
                - votes.get("negative", 0)
            )

            published_at = item.get("published_at", "")
            try:
                timestamp = datetime.fromisoformat(published_at.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                timestamp = datetime.utcnow()

            post = SocialPost(
                source="cryptopanic",
                text=text,
                author=item.get("domain", "news"),
                timestamp=timestamp,
                engagement=max(engagement, 0),
                post_id=post_id,
            )

            self._posts_collected += 1
            new_count += 1

            if self._on_post:
                await self._on_post(post)

        if new_count > 0:
            log.info("CryptoPanic posts collected", new=new_count)


# ─── Fear & Greed Index ───────────────────────────────────────────────────────

@dataclass
class FearGreedReading:
    value: int          # 0-100
    classification: str # Extreme Fear, Fear, Neutral, Greed, Extreme Greed
    timestamp: datetime


class FearGreedCollector:
    """
    Polls alternative.me Fear & Greed Index — completely free, no auth.
    Provides a macro sentiment baseline for signal confidence weighting.
    """

    URL = "https://api.alternative.me/fng/?limit=1"

    def __init__(self, interval_s: int = 3600):  # hourly
        self.interval_s = interval_s
        self._latest: Optional[FearGreedReading] = None
        self._running = False

    @property
    def latest(self) -> Optional[FearGreedReading]:
        return self._latest

    @property
    def macro_bias(self) -> float:
        """Returns -1.0 (extreme fear) to +1.0 (extreme greed)."""
        if not self._latest:
            return 0.0
        return (self._latest.value - 50) / 50.0

    async def start(self, session: aiohttp.ClientSession) -> None:
        self._running = True
        log.info("Fear & Greed collector started")

        while self._running:
            try:
                await self._poll(session)
            except Exception as e:
                log.warning("Fear & Greed poll failed", error=str(e))
            await asyncio.sleep(self.interval_s)

    async def stop(self):
        self._running = False

    async def _poll(self, session: aiohttp.ClientSession) -> None:
        async with session.get(self.URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return
            data = await resp.json(content_type=None)

        entry = data.get("data", [{}])[0]
        self._latest = FearGreedReading(
            value=int(entry.get("value", 50)),
            classification=entry.get("value_classification", "Neutral"),
            timestamp=datetime.utcfromtimestamp(int(entry.get("timestamp", time.time()))),
        )
        log.info(
            "Fear & Greed updated",
            value=self._latest.value,
            classification=self._latest.classification,
        )
