"""
Push signals from Service 2 (pipeline) → Service 1 (API).

Set API_SERVICE_URL on Service 2 in Railway to enable.
Example: https://service-1-api-production-xxxx.up.railway.app
"""
import os
import asyncio
from datetime import datetime
from typing import Any, Dict
from utils.logging import get_logger

log = get_logger("utils.api_push")

_API_URL = os.getenv("API_SERVICE_URL", "").rstrip("/")
_PUSH_ENDPOINT = f"{_API_URL}/internal/push" if _API_URL else None
_session = None  # shared aiohttp session


async def _get_session():
    global _session
    if _session is None or _session.closed:
        import aiohttp
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=3),
        )
    return _session


def _serialize(obj: Any) -> Any:
    """Recursively convert dataclass/enum/datetime → JSON-safe types."""
    import dataclasses, enum
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat() + "Z"
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(i) for i in obj]
    return obj


async def push_signal(signal) -> None:
    """Fire-and-forget: serialize signal and POST to Service 1."""
    if not _PUSH_ENDPOINT:
        return  # API_SERVICE_URL not configured — silent skip

    try:
        payload = _serialize(signal)
        session = await _get_session()
        async with session.post(_PUSH_ENDPOINT, json=payload) as resp:
            if resp.status != 200:
                log.warning("api_push failed", status=resp.status, endpoint=_PUSH_ENDPOINT)
    except Exception as e:
        log.debug("api_push error (non-fatal)", error=str(e))
