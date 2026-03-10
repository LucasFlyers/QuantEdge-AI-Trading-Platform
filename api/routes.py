"""
Platform API Layer — FastAPI-based REST interface.

Exposes all signal data, engine stats, and live spreads.
Designed for dashboard consumption and external integrations.
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
from datetime import datetime
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.models import (
    ArbitrageSignal, SentimentSignal, LiquiditySignal,
    WhaleSignal, SignalType
)
from config.settings import get_neon, DEVELOPER
from data.storage.neon import init_pool, close_pool, run_migrations, get_repository
from utils.logging import get_logger

log = get_logger("api.router")

app = FastAPI(
    title="QuantEdge AI Trading Intelligence Platform",
    description=(
        "Real-time crypto market signal API\n\n"
        f"**Developer:** {DEVELOPER['name']}  \n"
        f"**LinkedIn:** [{DEVELOPER['linkedin']}]({DEVELOPER['linkedin']})"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

from fastapi.staticfiles import StaticFiles
app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Startup / Shutdown ───────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    """Initialise Neon connection pool and run schema migrations."""
    cfg = get_neon()
    if cfg.is_configured:
        await init_pool()
        await run_migrations()
        log.info("Neon database ready")
    else:
        log.warning(
            "DATABASE_URL not set — running without persistent storage. "
            "Set DATABASE_URL in environment to enable Neon."
        )


@app.on_event("shutdown")
async def on_shutdown():
    await close_pool()

# ─── State (injected by platform orchestrator) ─────────────────────────────

_arbitrage_pipeline = None
_signal_history: List[Dict] = []
MAX_HISTORY = 1000


def inject_pipeline(pipeline):
    global _arbitrage_pipeline
    _arbitrage_pipeline = pipeline


def record_signal(signal_dict: Dict):
    _signal_history.append(signal_dict)
    if len(_signal_history) > MAX_HISTORY:
        _signal_history.pop(0)


# ─── Response Schemas ─────────────────────────────────────────────────────────

class SpreadResponse(BaseModel):
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    gross_spread_bps: float
    timestamp: float


class SignalResponse(BaseModel):
    id: str
    signal_type: str
    strength: str
    confidence: float
    direction: str
    timestamp: str
    data: Dict[str, Any]


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    components: Dict[str, Any]


# ─── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/signals/arbitrage", tags=["Signals"])
async def get_arbitrage_signals(
    limit: int = Query(default=50, le=500),
    symbol: Optional[str] = Query(default=None),
    min_confidence: float = Query(default=0.0),
):
    """
    Get recent arbitrage signals.
    Returns in-memory signals first; falls back to Neon history if empty.
    """
    signals = [
        s for s in _signal_history
        if s.get("signal_type") == "arbitrage"
        and s.get("confidence", 0) >= min_confidence
        and (symbol is None or s.get("symbol") == symbol)
    ]
    if not signals and get_neon().is_configured:
        try:
            repo = get_repository()
            signals = await repo.get_recent_arbitrage(limit=limit, symbol=symbol, min_confidence=min_confidence)
        except Exception as e:
            log.error("Neon fallback query failed", error=str(e))
    return {"signals": signals[-limit:], "count": len(signals), "source": "live"}


@app.get("/signals/sentiment", tags=["Signals"])
async def get_sentiment_signals(
    limit: int = Query(default=50, le=500),
    token: Optional[str] = Query(default=None),
):
    """Get recent sentiment signals."""
    signals = [
        s for s in _signal_history
        if s.get("signal_type") == "sentiment"
        and (token is None or s.get("token") == token)
    ]
    return {"signals": signals[-limit:], "count": len(signals)}


@app.get("/signals/liquidity", tags=["Signals"])
async def get_liquidity_signals(
    limit: int = Query(default=50, le=500),
    symbol: Optional[str] = Query(default=None),
    exchange: Optional[str] = Query(default=None),
):
    """Get recent liquidity signals (walls, imbalances)."""
    signals = [
        s for s in _signal_history
        if s.get("signal_type") == "liquidity"
        and (symbol is None or s.get("symbol") == symbol)
        and (exchange is None or s.get("exchange") == exchange)
    ]
    return {"signals": signals[-limit:], "count": len(signals)}


@app.get("/signals/whales", tags=["Signals"])
async def get_whale_signals(
    limit: int = Query(default=50, le=500),
    asset: Optional[str] = Query(default=None),
    min_usd: float = Query(default=0),
):
    """Get recent whale transfer signals."""
    signals = [
        s for s in _signal_history
        if s.get("signal_type") == "whale"
        and (asset is None or s.get("asset") == asset)
        and s.get("amount_usd", 0) >= min_usd
    ]
    return {"signals": signals[-limit:], "count": len(signals)}


@app.get("/signals/all", tags=["Signals"])
async def get_all_signals(
    limit: int = Query(default=100, le=1000),
    signal_type: Optional[str] = Query(default=None),
):
    """Get all recent signals across all types."""
    signals = _signal_history
    if signal_type:
        signals = [s for s in signals if s.get("signal_type") == signal_type]
    return {
        "signals": signals[-limit:],
        "count": len(signals),
        "total_in_memory": len(_signal_history),
    }


@app.get("/scanner/arbitrage/live", tags=["Scanner"])
async def get_live_spreads():
    """
    Real-time arbitrage scanner — shows all current cross-exchange spreads.
    Even below signal threshold, for monitoring.
    """
    if not _arbitrage_pipeline:
        return {"spreads": [], "message": "Pipeline not running"}

    spreads = _arbitrage_pipeline.get_current_spreads()
    return {
        "spreads": [
            {
                "symbol": s.symbol,
                "buy_exchange": s.buy_exchange,
                "sell_exchange": s.sell_exchange,
                "buy_price": s.buy_price,
                "sell_price": s.sell_price,
                "gross_spread_bps": round(s.gross_spread_bps, 2),
                "timestamp": s.timestamp,
            }
            for s in spreads[:50]
        ],
        "count": len(spreads),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/engine/stats", tags=["System"])
async def get_engine_stats():
    """Pipeline and engine performance statistics."""
    if not _arbitrage_pipeline:
        return {"error": "Pipeline not initialized"}

    return {
        "arbitrage": _arbitrage_pipeline.get_engine_stats(),
        "signals_in_memory": len(_signal_history),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/", tags=["System"])
async def root():
    return {
        "platform": "QuantEdge AI Trading Intelligence Platform",
        "version": "1.0.0",
        "developer": DEVELOPER,
        "docs": "/docs",
        "status": "operational",
        "database": "neon" if get_neon().is_configured else "not configured",
        "endpoints": [
            "/signals/arbitrage",
            "/signals/sentiment",
            "/signals/liquidity",
            "/signals/whales",
            "/signals/all",
            "/scanner/arbitrage/live",
            "/engine/stats",
            "/health",
        ]
    }


@app.get("/health", tags=["System"])
async def health():
    """
    Health check endpoint — used by Railway to verify the service is running.
    Always returns 200 so long as the process is alive.
    Database connectivity is checked separately and reported but does not
    cause a health failure (avoids restart loops on DB issues).
    """
    db_status = "unconfigured"
    if get_neon().is_configured:
        try:
            from data.storage.neon import get_pool
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_status = "connected"
        except Exception:
            db_status = "error"

    return {
        "status": "ok",
        "database": db_status,
        "signals_buffered": len(_signal_history),
        "pipeline_active": _arbitrage_pipeline is not None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
