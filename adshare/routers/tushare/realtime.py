"""Tushare Pro compatible realtime endpoints (``rt_k`` / ``rt_min``).

Reads the realtime data that the worker (:mod:`amazingdata.realtime`)
writes into Redis and repackages it in the tushare Pro response shape:

* ``rt_k``   — Level-1 snapshot, from the ``REALTIME_QUOTE_KEY`` single key
* ``rt_min`` — recent minute kline bars, from the
  ``REALTIME_KLINE_HIST_KEY`` Redis Stream

Both handlers are registered in ``HANDLERS`` for the unified
``POST /tushare`` entry point and exposed as REST routes under
``/tushare/realtime/*``. They ignore the ``service``/``up_service``/
``down_service`` kwargs injected by the unified entry and go through
``get_cache_manager()`` directly.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Request

from adshare.core.cache import get_cache_manager
from adshare.core.config import get_settings
from adshare.core.exceptions import InvalidParameterError
from adshare.core.logging import get_logger
from adshare.core.realtime_keys import (
    REALTIME_KLINE_HIST_KEY,
    REALTIME_QUOTE_KEY,
)
from adshare.routers.tushare.common import (
    extract_tushare_params,
    handle_tushare_exception,
    parse_code_param,
    parse_int_param,
    parse_request_body,
    tushare_empty,
    tushare_success,
)
from adshare.services.realtime_tushare_mapper import (
    FREQ_TO_PERIOD,
    PERIOD_TO_FREQ,
    kline_columns,
    kline_to_tushare_row,
    quote_columns,
    quote_to_tushare_row,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/realtime", tags=["tushare-realtime"])


# ---------------------------------------------------------------------------
# Parameter helpers
# ---------------------------------------------------------------------------


def _resolve_freq(value: Any) -> tuple[str, str]:
    """Resolve a tushare freq label to (tushare label, internal period)."""
    if value is None or not str(value).strip():
        raise InvalidParameterError(
            f"freq is required, expected one of {list(FREQ_TO_PERIOD)}"
        )
    text = str(value).strip().upper()
    if text in FREQ_TO_PERIOD:
        return text, FREQ_TO_PERIOD[text]
    # Also accept the internal period spelling (min1/min5/...) for convenience
    text_lower = str(value).strip().lower()
    if text_lower in PERIOD_TO_FREQ:
        return PERIOD_TO_FREQ[text_lower], text_lower
    raise InvalidParameterError(
        f"Invalid freq: {value}, expected one of {list(FREQ_TO_PERIOD)}"
    )


def _parse_time_param(value: Any, name: str) -> Optional[datetime]:
    """Parse an optional start_time/end_time filter to a datetime."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.isdigit():
            return datetime.fromtimestamp(int(text) / 1000)
        return datetime.fromisoformat(text)
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        raise InvalidParameterError(
            f"Invalid {name} format: {value}, expected 'YYYY-MM-DD HH:MM:SS'"
        ) from exc


def _bar_time(payload: dict) -> Optional[datetime]:
    """Best-effort parse of a kline payload's bar time (None on failure)."""
    raw = payload.get("kline_time") or payload.get("trade_time")
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw / 1000)
        return datetime.fromisoformat(str(raw).strip())
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _apply_fields(columns: list[str], fields: Optional[list[str]]) -> list[str]:
    """Restrict output columns to the requested fields (if recognizable)."""
    if not fields:
        return columns
    available = [f for f in fields if f in columns]
    return available or columns


def _rows_to_payload(
    columns: list[str], rows: list[dict], fields: Optional[list[str]]
) -> dict[str, Any]:
    """Build the tushare Pro payload from mapped rows."""
    if not rows:
        return tushare_empty()
    columns = _apply_fields(columns, fields)
    items = [[row.get(col) for col in columns] for row in rows]
    return tushare_success(fields=columns, items=items)


# ---------------------------------------------------------------------------
# Core handlers (used by both RESTful routes and the unified /tushare entry)
# ---------------------------------------------------------------------------


def handle_rt_k(
    params: dict[str, Any], fields: Optional[list[str]], **kwargs
) -> dict[str, Any]:
    """Tushare Pro ``rt_k``: latest Level-1 snapshot for given stock(s)."""
    codes = parse_code_param(params.get("ts_code"))
    if not codes:
        raise InvalidParameterError("ts_code is required")

    cache = get_cache_manager()
    rows = []
    for code in codes:
        data = cache.get_realtime_market(REALTIME_QUOTE_KEY, code)
        if data is None:
            logger.info("rt_k: no realtime snapshot cached for %s", code)
            continue
        rows.append(quote_to_tushare_row(code, data))
    return _rows_to_payload(quote_columns(), rows, fields)


def handle_rt_min(
    params: dict[str, Any], fields: Optional[list[str]], **kwargs
) -> dict[str, Any]:
    """Tushare Pro ``rt_min``: recent minute kline bars for a single stock."""
    codes = parse_code_param(params.get("ts_code"))
    if not codes:
        raise InvalidParameterError("ts_code is required")
    if len(codes) > 1:
        raise InvalidParameterError("rt_min supports a single ts_code")
    code = codes[0]

    freq, period = _resolve_freq(params.get("freq"))
    limit = parse_int_param(params.get("limit"), "limit")
    if limit is None or limit <= 0:
        limit = get_settings().realtime_kline_max_bars
    start_time = _parse_time_param(params.get("start_time"), "start_time")
    end_time = _parse_time_param(params.get("end_time"), "end_time")

    cache = get_cache_manager()
    stream_key = cache._make_key(
        "realtime", f"{REALTIME_KLINE_HIST_KEY}:{period}", code
    )
    try:
        entries = cache.redis.xrevrange(stream_key, "+", "-", count=limit)
    except Exception as exc:
        logger.error("rt_min: XREVRANGE failed for %s: %s", stream_key, exc)
        return tushare_empty()

    rows = []
    for payload in _decode_stream_entries(entries):
        bar_dt = _bar_time(payload)
        if bar_dt is not None:
            if start_time is not None and bar_dt < start_time:
                continue
            if end_time is not None and bar_dt > end_time:
                continue
        rows.append(kline_to_tushare_row(code, freq, payload))

    rows.reverse()  # XREVRANGE is newest-first; tushare returns ascending
    return _rows_to_payload(kline_columns(), rows, fields)


def _decode_stream_entries(entries: list) -> list[dict]:
    """Decode raw XREVRANGE entries into kline payload dicts."""
    rows = []
    for _entry_id, raw_fields in entries or []:
        decoded = {}
        for key, value in raw_fields.items():
            k = key.decode() if isinstance(key, bytes) else key
            v = value.decode() if isinstance(value, bytes) else value
            decoded[k] = v
        data = decoded.get("data")
        if not data:
            continue
        try:
            rows.append(json.loads(data))
        except (ValueError, TypeError):
            continue
    return rows


# ---------------------------------------------------------------------------
# RESTful route wrappers
# ---------------------------------------------------------------------------


async def _extract_from_request(
    request: Request, api_name: str
) -> tuple[dict[str, Any], Optional[list[str]]]:
    """Parse request body and return (params, fields)."""
    body = await parse_request_body(request)
    _, params, fields, _ = extract_tushare_params({**body, "api_name": api_name})
    return params, fields


@router.post("/rt_k")
@router.get("/rt_k")
async def tushare_rt_k(request: Request):
    """Tushare Pro ``rt_k`` endpoint."""
    try:
        params, fields = await _extract_from_request(request, "rt_k")
        return handle_rt_k(params, fields)
    except Exception as exc:
        return handle_tushare_exception(exc)


@router.post("/rt_min")
@router.get("/rt_min")
async def tushare_rt_min(request: Request):
    """Tushare Pro ``rt_min`` endpoint."""
    try:
        params, fields = await _extract_from_request(request, "rt_min")
        return handle_rt_min(params, fields)
    except Exception as exc:
        return handle_tushare_exception(exc)


# ---------------------------------------------------------------------------
# Handler registry for the unified /tushare entry point
# ---------------------------------------------------------------------------


HANDLERS: dict[str, Any] = {
    "rt_k": handle_rt_k,
    "rt_min": handle_rt_min,
}
