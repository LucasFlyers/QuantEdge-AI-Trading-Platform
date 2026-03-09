"""
Structured Logging — JSON-formatted logs with context propagation.
Production-grade observability layer.
"""
import logging
import json
import sys
import traceback
from datetime import datetime
from typing import Any, Dict, Optional
from contextvars import ContextVar
from functools import wraps
import time

# Context variables for request tracing
_request_id: ContextVar[str] = ContextVar("request_id", default="")
_component: ContextVar[str] = ContextVar("component", default="platform")


class JSONFormatter(logging.Formatter):
    """Emit structured JSON log records for aggregation pipelines."""

    LEVEL_MAP = {
        logging.DEBUG: "debug",
        logging.INFO: "info",
        logging.WARNING: "warning",
        logging.ERROR: "error",
        logging.CRITICAL: "critical",
    }

    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": self.LEVEL_MAP.get(record.levelno, "info"),
            "component": _component.get() or record.name,
            "message": record.getMessage(),
            "logger": record.name,
            "pid": record.process,
        }

        # Request tracing
        req_id = _request_id.get()
        if req_id:
            log_data["request_id"] = req_id

        # Extra fields from structured logging calls
        if hasattr(record, "extra"):
            log_data.update(record.extra)

        # Exception info
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }

        return json.dumps(log_data, default=str)


class StructuredLogger:
    """
    Thin wrapper over stdlib logger that supports structured field injection.
    Usage:
        log = get_logger("arbitrage.scanner")
        log.info("Spread detected", symbol="ETH/USDT", spread_bps=45.2)
    """

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def _log(self, level: int, message: str, **fields):
        if self._logger.isEnabledFor(level):
            extra = {"extra": fields} if fields else {}
            record = self._logger.makeRecord(
                self._logger.name, level, "(unknown)", 0,
                message, (), None, extra=extra
            )
            # Attach extra fields directly
            if fields:
                record.extra = fields
            self._logger.handle(record)

    def debug(self, msg: str, **kw): self._log(logging.DEBUG, msg, **kw)
    def info(self, msg: str, **kw): self._log(logging.INFO, msg, **kw)
    def warning(self, msg: str, **kw): self._log(logging.WARNING, msg, **kw)
    def error(self, msg: str, **kw): self._log(logging.ERROR, msg, **kw)
    def critical(self, msg: str, **kw): self._log(logging.CRITICAL, msg, **kw)

    def exception(self, msg: str, **kw):
        kw["exc_info"] = True
        self._log(logging.ERROR, msg, **kw)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Bootstrap logging for the entire platform process."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        ))

    root.handlers.clear()
    root.addHandler(handler)


def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(name)


def set_request_context(request_id: str, component: str = "") -> None:
    _request_id.set(request_id)
    if component:
        _component.set(component)


# ─── Decorators ───────────────────────────────────────────────────────────────

def log_execution_time(logger: Optional[StructuredLogger] = None):
    """Decorator: logs function execution duration."""
    def decorator(func):
        _log = logger or get_logger(func.__module__)

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000
                _log.debug(
                    f"{func.__name__} completed",
                    elapsed_ms=round(elapsed_ms, 2)
                )
                return result
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start) * 1000
                _log.error(
                    f"{func.__name__} failed",
                    elapsed_ms=round(elapsed_ms, 2),
                    error=str(e)
                )
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000
                _log.debug(
                    f"{func.__name__} completed",
                    elapsed_ms=round(elapsed_ms, 2)
                )
                return result
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start) * 1000
                _log.error(
                    f"{func.__name__} failed",
                    elapsed_ms=round(elapsed_ms, 2),
                    error=str(e)
                )
                raise

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
