"""Service-status routers.

Aggregates data published by the amazingdata containers (heartbeat
keys, counter streams, data-freshness rows) and serves it as JSON
for the in-browser dashboard.

All routes require the same ``X-API-Key`` auth as the rest of the
protected surface (see ``adshare.core.auth.require_connection_auth``)
and emit ``Cache-Control: no-store`` so intermediary caches cannot
serve stale "down" states.

Liveness signals
----------------
- ``adshare-api`` is alive iff this handler is responding.
- ``amazingdata-realtime`` / ``amazingdata-batch``: derived from
  Redis key TTL on ``adshare:heartbeat:<svc>`` (each container
  refreshes every 10 s with a 30 s TTL).
- ``redis``: alive iff at least one heartbeat key read succeeded in
  this request. If Redis is fully down, the whole handler raises.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Response

from adshare import dependencies as deps
from adshare.core.cache import CacheManager
from adshare.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/status", tags=["status"])

_HEARTBEAT_KEYS = {
    "amazingdata-realtime": "adshare:heartbeat:amazingdata-realtime",
    "amazingdata-batch": "adshare:heartbeat:amazingdata-batch",
}
_HISTORY_STREAMS = {
    "amazingdata-realtime": "adshare:stats:amazingdata-realtime",
    "amazingdata-batch": "adshare:stats:amazingdata-batch",
}
_FRESHNESS_KEYS = {
    "day": "adshare:data:freshness:kline:day",
    "week": "adshare:data:freshness:kline:week",
    "month": "adshare:data:freshness:kline:month",
}
_JOB_FAILURES_HASH = "adshare:data:freshness:job:failures"
_STATUS_EVENTS_STREAM = "adshare:status:events"


async def _no_cache(response: Response) -> None:
    """Disable caching on all status responses."""
    response.headers["Cache-Control"] = "no-store"


def _read_heartbeat(
    cache: CacheManager, key: str, now: float,
) -> Optional[Dict[str, Any]]:
    """Read one heartbeat key. Returns None on missing/decode error,
    or a parsed dict with ``ts`` and ``age_sec`` fields added."""
    try:
        raw = cache.redis.get(key)
    except Exception as e:
        logger.warning("heartbeat read failed for %s: %s", key, e)
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    ts = payload.get("ts")
    try:
        age_sec = max(0.0, now - float(ts)) if ts is not None else None
    except (TypeError, ValueError):
        age_sec = None
    payload["age_sec"] = age_sec
    payload["last_seen_at"] = ts
    return payload


def _derive_status(age_sec: Optional[float]) -> str:
    """Map a heartbeat age to a coarse status string.

    The dashboard applies its own M-of-N smoothing on top of this;
    this is just a single-sample hint.
    """
    if age_sec is None:
        return "down"
    if age_sec < 30:
        return "ok"
    if age_sec < 90:
        return "degraded"
    return "down"


@router.get("", dependencies=[Depends(_no_cache)])
async def get_status_composite(
    cache: CacheManager = Depends(deps.get_cache_manager_dep),
) -> Dict[str, Any]:
    """Composite snapshot the dashboard polls every 7 s."""
    now = time.time()
    services = _build_services(cache, now)
    realtime_stats = _build_realtime_stats(cache, now)
    data_freshness = _build_data_freshness(cache)
    return {
        "ts": now,
        "services": services,
        "realtime_stats": realtime_stats,
        "data_freshness": data_freshness,
    }


@router.get("/services", dependencies=[Depends(_no_cache)])
async def get_services(
    cache: CacheManager = Depends(deps.get_cache_manager_dep),
) -> Dict[str, Any]:
    """Per-service liveness derived from heartbeat keys."""
    now = time.time()
    return {"ts": now, "services": _build_services(cache, now)}


@router.get("/realtime/stats", dependencies=[Depends(_no_cache)])
async def get_realtime_stats(
    cache: CacheManager = Depends(deps.get_cache_manager_dep),
) -> Dict[str, Any]:
    """Current realtime counters + 30-minute history (1-min buckets)."""
    now = time.time()
    return {"ts": now, "stats": _build_realtime_stats(cache, now)}


@router.get("/data-freshness", dependencies=[Depends(_no_cache)])
async def get_data_freshness(
    cache: CacheManager = Depends(deps.get_cache_manager_dep),
) -> Dict[str, Any]:
    """Per-period kline freshness rows + recent job failures."""
    return {"ts": time.time(), "freshness": _build_data_freshness(cache)}


@router.get("/events", dependencies=[Depends(_no_cache)])
async def get_status_events(
    count: int = 50,
    cache: CacheManager = Depends(deps.get_cache_manager_dep),
) -> Dict[str, Any]:
    """Debug-only: read recent entries from the status-events stream.

    Stream is not currently populated by any handler — kept for a
    future state-machine logger that records transitions for
    postmortem.
    """
    try:
        entries = cache.redis.xrevrange(_STATUS_EVENTS_STREAM, count=count)
    except Exception as e:
        logger.warning("status events read failed: %s", e)
        entries = []
    decoded: List[Dict[str, Any]] = []
    for entry_id, fields in entries:
        decoded.append({
            "id": entry_id.decode() if isinstance(entry_id, bytes) else entry_id,
            "fields": {
                k.decode() if isinstance(k, bytes) else k:
                v.decode() if isinstance(v, bytes) else v
                for k, v in fields.items()
            },
        })
    return {"ts": time.time(), "events": decoded}


# ---------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------


def _build_services(
    cache: CacheManager, now: float,
) -> List[Dict[str, Any]]:
    services: List[Dict[str, Any]] = []

    # adshare-api is alive iff this handler is running.
    services.append({
        "name": "adshare-api",
        "alive": True,
        "age_sec": 0.0,
        "last_seen_at": now,
        "status": "ok",
    })

    redis_alive = True
    for svc_name, key in _HEARTBEAT_KEYS.items():
        hb = _read_heartbeat(cache, key, now)
        if hb is None:
            services.append({
                "name": svc_name,
                "alive": False,
                "age_sec": None,
                "last_seen_at": None,
                "status": "down",
            })
            continue
        age_sec = hb.get("age_sec")
        status = _derive_status(age_sec)
        alive = isinstance(age_sec, (int, float)) and age_sec < 30
        services.append({
            "name": svc_name,
            "alive": alive,
            "age_sec": round(age_sec, 2) if isinstance(age_sec, (int, float)) else None,
            "last_seen_at": hb.get("last_seen_at"),
            "status": status,
            "payload": hb,
        })

    services.append({
        "name": "redis",
        "alive": redis_alive,
        "age_sec": 0.0,
        "last_seen_at": now if redis_alive else None,
        "status": "ok" if redis_alive else "down",
    })

    return services


def _build_realtime_stats(
    cache: CacheManager, now: float,
) -> Dict[str, Any]:
    """Realtime counters for both services + their counter histories."""
    out: Dict[str, Any] = {}
    for svc_name, hb_key in _HEARTBEAT_KEYS.items():
        hb = _read_heartbeat(cache, hb_key, now)
        out[svc_name] = {
            "heartbeat": hb,
            "history": _read_history(cache, _HISTORY_STREAMS[svc_name]),
        }
    return out


def _read_history(
    cache: CacheManager, stream_key: str, count: int = 30,
) -> List[Dict[str, Any]]:
    try:
        entries = cache.redis.xrevrange(stream_key, count=count)
    except Exception as e:
        logger.warning("history read failed for %s: %s", stream_key, e)
        return []
    out: List[Dict[str, Any]] = []
    for entry_id, fields in entries:
        raw = fields.get(b"data") or fields.get("data")
        if raw is None:
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            entry = json.loads(raw)
        except (ValueError, TypeError):
            continue
        entry["_entry_id"] = (
            entry_id.decode() if isinstance(entry_id, bytes) else entry_id
        )
        out.append(entry)
    out.reverse()
    return out


def _build_data_freshness(cache: CacheManager) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for period, key in _FRESHNESS_KEYS.items():
        row = _read_json(cache, key)
        if row is None:
            rows.append({
                "period": period,
                "missing": True,
                "last_run_status": "unknown",
            })
        else:
            rows.append({"period": period, "missing": False, **row})
    failures: Dict[str, str] = {}
    try:
        raw = cache.redis.hgetall(_JOB_FAILURES_HASH)
    except Exception as e:
        logger.warning("failures read failed: %s", e)
        raw = {}
    for k, v in raw.items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        try:
            failures[key] = json.loads(val)
        except (ValueError, TypeError):
            failures[key] = val
    return {"rows": rows, "failures": failures}


def _read_json(cache: CacheManager, key: str) -> Optional[Dict[str, Any]]:
    try:
        raw = cache.redis.get(key)
    except Exception as e:
        logger.warning("freshness read failed for %s: %s", key, e)
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None