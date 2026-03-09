"""
Neon Database Client — Async connection pool for serverless PostgreSQL.

Uses asyncpg directly (no ORM overhead) for maximum throughput.
Connection pooling is handled by Neon's built-in PgBouncer endpoint —
set DATABASE_URL to the *pooled* connection string from your Neon console.

Key considerations for Neon:
  - statement_cache_size=0  (required for PgBouncer session mode)
  - sslmode=require          (always enforced)
  - Pool max_size ≤ 10       (free tier limit; upgrade for more)
  - Connections idle >5min   are recycled to avoid Neon's auto-suspend
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional
import sys, os

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config.settings import get_neon
from utils.logging import get_logger

log = get_logger("data.storage.neon")

# ─── Module-level pool singleton ─────────────────────────────────────────────
_pool: Optional[asyncpg.Pool] = None


async def init_pool() -> asyncpg.Pool:
    """
    Initialise the connection pool. Call once at startup.
    Safe to call multiple times — returns existing pool if already created.
    """
    global _pool
    if _pool is not None:
        return _pool

    cfg = get_neon()

    log.info("Connecting to Neon PostgreSQL", dsn_masked=cfg.dsn[:50] + "…")

    _pool = await asyncpg.create_pool(
        dsn=cfg.dsn,
        min_size=cfg.pool_min_size,
        max_size=cfg.pool_max_size,
        max_inactive_connection_lifetime=cfg.pool_max_inactive_lifetime,
        statement_cache_size=cfg.statement_cache_size,   # must be 0 for PgBouncer
        command_timeout=30,
    )

    log.info("Neon connection pool ready",
             min_size=cfg.pool_min_size, max_size=cfg.pool_max_size)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("Neon connection pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised. Call init_pool() first.")
    return _pool


@asynccontextmanager
async def acquire():
    """Context manager: acquire a connection from the pool."""
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


# ─── Schema Bootstrap ─────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- ── Arbitrage signals ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS arbitrage_signals (
    id              TEXT        PRIMARY KEY,
    symbol          TEXT        NOT NULL,
    buy_exchange    TEXT        NOT NULL,
    sell_exchange   TEXT        NOT NULL,
    buy_price       NUMERIC     NOT NULL,
    sell_price      NUMERIC     NOT NULL,
    spread_bps      NUMERIC     NOT NULL,
    gross_profit_bps NUMERIC    NOT NULL,
    net_profit_bps  NUMERIC     NOT NULL,
    fee_bps         NUMERIC     NOT NULL,
    slippage_bps    NUMERIC     NOT NULL,
    max_size_usd    NUMERIC     NOT NULL,
    profit_usd      NUMERIC     NOT NULL,
    confidence      NUMERIC     NOT NULL,
    strength        TEXT        NOT NULL,
    direction       TEXT        NOT NULL,
    execution_window_ms INT     NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_arb_symbol     ON arbitrage_signals (symbol);
CREATE INDEX IF NOT EXISTS idx_arb_created_at ON arbitrage_signals (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_arb_net_profit ON arbitrage_signals (net_profit_bps DESC);

-- ── Sentiment signals ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sentiment_signals (
    id                  TEXT        PRIMARY KEY,
    token               TEXT        NOT NULL,
    mention_count       INT         NOT NULL,
    mention_change_pct  NUMERIC     NOT NULL,
    sentiment_score     NUMERIC     NOT NULL,
    bullish_pct         NUMERIC     NOT NULL,
    bearish_pct         NUMERIC     NOT NULL,
    neutral_pct         NUMERIC     NOT NULL,
    direction           TEXT        NOT NULL,
    confidence          NUMERIC     NOT NULL,
    strength            TEXT        NOT NULL,
    lookback_hours      INT         NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sent_token      ON sentiment_signals (token);
CREATE INDEX IF NOT EXISTS idx_sent_created_at ON sentiment_signals (created_at DESC);

-- ── Liquidity signals ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS liquidity_signals (
    id              TEXT        PRIMARY KEY,
    symbol          TEXT        NOT NULL,
    exchange        TEXT        NOT NULL,
    wall_side       TEXT        NOT NULL,
    wall_price      NUMERIC     NOT NULL,
    wall_size_usd   NUMERIC     NOT NULL,
    wall_size_base  NUMERIC     NOT NULL,
    imbalance_ratio NUMERIC     NOT NULL,
    bid_depth_usd   NUMERIC     NOT NULL,
    ask_depth_usd   NUMERIC     NOT NULL,
    direction       TEXT        NOT NULL,
    confidence      NUMERIC     NOT NULL,
    strength        TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_liq_symbol     ON liquidity_signals (symbol);
CREATE INDEX IF NOT EXISTS idx_liq_exchange   ON liquidity_signals (exchange);
CREATE INDEX IF NOT EXISTS idx_liq_created_at ON liquidity_signals (created_at DESC);

-- ── Whale signals ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS whale_signals (
    id                  TEXT        PRIMARY KEY,
    asset               TEXT        NOT NULL,
    from_address        TEXT        NOT NULL,
    to_address          TEXT        NOT NULL,
    amount              NUMERIC     NOT NULL,
    amount_usd          NUMERIC     NOT NULL,
    move_type           TEXT        NOT NULL,
    exchange_name       TEXT,
    tx_hash             TEXT        NOT NULL,
    chain               TEXT        NOT NULL DEFAULT 'ethereum',
    historical_pattern  TEXT,
    direction           TEXT        NOT NULL,
    confidence          NUMERIC     NOT NULL,
    strength            TEXT        NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_whale_asset      ON whale_signals (asset);
CREATE INDEX IF NOT EXISTS idx_whale_created_at ON whale_signals (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_whale_amount_usd ON whale_signals (amount_usd DESC);
CREATE INDEX IF NOT EXISTS idx_whale_tx         ON whale_signals (tx_hash);

-- ── Price ticks (rolling 24h only — older rows purged by a job) ────────────
CREATE TABLE IF NOT EXISTS price_ticks (
    id          BIGSERIAL   PRIMARY KEY,
    exchange    TEXT        NOT NULL,
    symbol      TEXT        NOT NULL,
    bid         NUMERIC     NOT NULL,
    ask         NUMERIC     NOT NULL,
    last        NUMERIC     NOT NULL,
    volume_24h  NUMERIC     NOT NULL DEFAULT 0,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ticks_exchange_symbol
    ON price_ticks (exchange, symbol, recorded_at DESC);

-- ── Alert log ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alert_log (
    id          TEXT        PRIMARY KEY,
    signal_id   TEXT        NOT NULL,
    signal_type TEXT        NOT NULL,
    channel     TEXT        NOT NULL,
    message     TEXT        NOT NULL,
    priority    INT         NOT NULL DEFAULT 1,
    delivered   BOOLEAN     NOT NULL DEFAULT FALSE,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_signal_id  ON alert_log (signal_id);
CREATE INDEX IF NOT EXISTS idx_alerts_channel    ON alert_log (channel);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alert_log (sent_at DESC);
"""


async def run_migrations() -> None:
    """Apply schema to Neon. Idempotent — safe to call on every startup."""
    async with acquire() as conn:
        await conn.execute(SCHEMA_SQL)
    log.info("Neon schema migrations applied")


# ─── Repository helpers ───────────────────────────────────────────────────────

class SignalRepository:
    """
    Thin async repository for persisting signals to Neon.
    All methods are fire-and-forget safe — errors are logged, not raised,
    so a DB write failure never takes down the signal pipeline.
    """

    async def save_arbitrage(self, signal) -> None:
        try:
            async with acquire() as conn:
                await conn.execute("""
                    INSERT INTO arbitrage_signals
                        (id, symbol, buy_exchange, sell_exchange, buy_price, sell_price,
                         spread_bps, gross_profit_bps, net_profit_bps, fee_bps,
                         slippage_bps, max_size_usd, profit_usd, confidence,
                         strength, direction, execution_window_ms, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
                    ON CONFLICT (id) DO NOTHING
                """,
                    signal.id, signal.symbol, signal.buy_exchange, signal.sell_exchange,
                    signal.buy_price, signal.sell_price, signal.spread_bps,
                    signal.gross_profit_bps, signal.net_profit_bps,
                    signal.estimated_fee_bps, signal.estimated_slippage_bps,
                    signal.max_tradeable_size_usd, signal.estimated_profit_usd,
                    signal.confidence, signal.strength.value, signal.direction.value,
                    signal.execution_window_ms, signal.timestamp,
                )
        except Exception as e:
            log.error("Failed to persist arbitrage signal", signal_id=signal.id, error=str(e))

    async def save_sentiment(self, signal) -> None:
        try:
            async with acquire() as conn:
                await conn.execute("""
                    INSERT INTO sentiment_signals
                        (id, token, mention_count, mention_change_pct, sentiment_score,
                         bullish_pct, bearish_pct, neutral_pct, direction,
                         confidence, strength, lookback_hours, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    ON CONFLICT (id) DO NOTHING
                """,
                    signal.id, signal.token, signal.mention_count,
                    signal.mention_change_pct, signal.sentiment_score,
                    signal.bullish_pct, signal.bearish_pct, signal.neutral_pct,
                    signal.direction.value, signal.confidence,
                    signal.strength.value, signal.lookback_hours, signal.timestamp,
                )
        except Exception as e:
            log.error("Failed to persist sentiment signal", signal_id=signal.id, error=str(e))

    async def save_whale(self, signal) -> None:
        try:
            async with acquire() as conn:
                await conn.execute("""
                    INSERT INTO whale_signals
                        (id, asset, from_address, to_address, amount, amount_usd,
                         move_type, exchange_name, tx_hash, chain,
                         historical_pattern, direction, confidence, strength, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                    ON CONFLICT (id) DO NOTHING
                """,
                    signal.id, signal.asset, signal.from_address, signal.to_address,
                    signal.amount, signal.amount_usd, signal.move_type.value,
                    signal.exchange_name, signal.tx_hash, signal.chain,
                    signal.historical_pattern, signal.direction.value,
                    signal.confidence, signal.strength.value, signal.timestamp,
                )
        except Exception as e:
            log.error("Failed to persist whale signal", signal_id=signal.id, error=str(e))

    async def save_liquidity(self, signal) -> None:
        try:
            async with acquire() as conn:
                await conn.execute("""
                    INSERT INTO liquidity_signals
                        (id, symbol, exchange, wall_side, wall_price, wall_size_usd,
                         wall_size_base, imbalance_ratio, bid_depth_usd, ask_depth_usd,
                         direction, confidence, strength, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                    ON CONFLICT (id) DO NOTHING
                """,
                    signal.id, signal.symbol, signal.exchange, signal.wall_side.value,
                    signal.wall_price, signal.wall_size_usd, signal.wall_size_base,
                    signal.imbalance_ratio, signal.bid_depth_usd, signal.ask_depth_usd,
                    signal.direction.value, signal.confidence,
                    signal.strength.value, signal.timestamp,
                )
        except Exception as e:
            log.error("Failed to persist liquidity signal", signal_id=signal.id, error=str(e))

    async def save_alert_log(self, signal_id: str, signal_type: str,
                              channel: str, message: str,
                              priority: int, delivered: bool) -> None:
        import uuid
        try:
            async with acquire() as conn:
                await conn.execute("""
                    INSERT INTO alert_log
                        (id, signal_id, signal_type, channel, message, priority, delivered)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    ON CONFLICT (id) DO NOTHING
                """,
                    str(uuid.uuid4()), signal_id, signal_type,
                    channel, message[:4000], priority, delivered,
                )
        except Exception as e:
            log.error("Failed to persist alert log", error=str(e))

    async def get_recent_arbitrage(
        self, limit: int = 50, symbol: Optional[str] = None,
        min_confidence: float = 0.0
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT * FROM arbitrage_signals
            WHERE confidence >= $1
            {symbol_filter}
            ORDER BY created_at DESC
            LIMIT $2
        """
        try:
            async with acquire() as conn:
                if symbol:
                    rows = await conn.fetch(
                        query.format(symbol_filter="AND symbol = $3"),
                        min_confidence, limit, symbol,
                    )
                else:
                    rows = await conn.fetch(
                        query.format(symbol_filter=""),
                        min_confidence, limit,
                    )
                return [dict(r) for r in rows]
        except Exception as e:
            log.error("Failed to query arbitrage signals", error=str(e))
            return []

    async def get_recent_whale(
        self, limit: int = 50, asset: Optional[str] = None,
        min_usd: float = 0
    ) -> List[Dict[str, Any]]:
        try:
            async with acquire() as conn:
                if asset:
                    rows = await conn.fetch(
                        "SELECT * FROM whale_signals WHERE asset=$1 AND amount_usd>=$2 ORDER BY created_at DESC LIMIT $3",
                        asset, min_usd, limit,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT * FROM whale_signals WHERE amount_usd>=$1 ORDER BY created_at DESC LIMIT $2",
                        min_usd, limit,
                    )
                return [dict(r) for r in rows]
        except Exception as e:
            log.error("Failed to query whale signals", error=str(e))
            return []


# ─── Singleton repository ─────────────────────────────────────────────────────
_repo: Optional[SignalRepository] = None

def get_repository() -> SignalRepository:
    global _repo
    if _repo is None:
        _repo = SignalRepository()
    return _repo
