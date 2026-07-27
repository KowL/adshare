"""Common helpers for the tushare compatible router package."""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

import pandas as pd
from fastapi import Request
from starlette.responses import JSONResponse

from adshare.core.config import get_settings
from adshare.core.exceptions import (
    AdshareException,
    AuthenticationError,
    AuthorizationError,
    InvalidParameterError,
    map_exception_to_http_status,
)
from adshare.core.logging import get_logger

logger = get_logger(__name__)


# Tushare Pro reserves specific error codes; mirror the ones clients expect
# so an auth failure surfaces as `code: 20001/20002` instead of an HTTP 401.
_TUSHARE_ERR_AUTH_MISSING = 20001
_TUSHARE_ERR_AUTH_INVALID = 20002


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


async def check_tushare_auth(request: Request) -> "str | JSONResponse":
    """Validate the Tushare Pro token.

    Returns the token string on success. On failure, returns a
    ``JSONResponse`` with HTTP **200** carrying the Tushare Pro error
    envelope — clients like the official ``tushare`` SDK inspect ``code``
    rather than HTTP status, so the body must be returned even for auth
    errors.
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return ""

    token = ""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json() or {}
            token = body.get("token", "")
        except Exception:
            token = ""

    if not token:
        token = request.headers.get("X-API-Key", "")
    if not token:
        token = request.query_params.get("api_key", "")

    if not token:
        return tushare_error_response(
            "API key required. Pass token in body, X-API-Key header or api_key query parameter.",
            code=_TUSHARE_ERR_AUTH_MISSING,
        )

    valid_key = settings.api_key
    if not valid_key:
        return tushare_error_response(
            "Server misconfiguration: API key not set",
            code=_TUSHARE_ERR_AUTH_INVALID,
        )
    if token != valid_key:
        return tushare_error_response("Invalid API key", code=_TUSHARE_ERR_AUTH_INVALID)

    return token


# Backward-compat shim — kept so any external caller that still imports the
# old name doesn't break. The FastAPI dependency binding is removed; the
# unified entry calls ``check_tushare_auth`` directly so the response can
# stay HTTP 200 per the Tushare Pro protocol.
async def tushare_auth(request: Request) -> str:
    """Deprecated. Use :func:`check_tushare_auth` instead."""
    result = await check_tushare_auth(request)
    if isinstance(result, JSONResponse):
        raise AuthenticationError(
            "auth failed (call check_tushare_auth directly to get the JSONResponse)"
        )
    return result


# ---------------------------------------------------------------------------
# Response formatting
# ---------------------------------------------------------------------------


def tushare_success(fields: Optional[Sequence[str]] = None, items: Optional[list] = None) -> dict[str, Any]:
    """Build a successful tushare Pro response payload."""
    return {
        "code": 0,
        "msg": "Success",
        "data": {
            "fields": list(fields or []),
            "items": list(items or []),
        },
    }


def tushare_empty() -> dict[str, Any]:
    """Build an empty successful tushare Pro response."""
    return tushare_success()


def tushare_error(msg: str, code: int = -1) -> dict[str, Any]:
    """Build a tushare Pro error envelope (raw dict — no HTTP status)."""
    return {"code": code, "msg": msg, "data": None}


def tushare_error_response(msg: str, code: int = -1) -> JSONResponse:
    """Build a tushare Pro error response with HTTP 200.

    Per the Tushare Pro protocol, error conditions are reported via the
    ``code`` field of the JSON body; the HTTP status must stay 200 so the
    official ``tushare`` SDK parses the envelope.
    """
    return JSONResponse(status_code=200, content=tushare_error(msg, code=code))


# Column names whose int values represent YYYYMMDD dates and must be
# serialised as strings in the response.
_DATE_STRING_COLUMNS = {
    "trade_date",
    "list_date",
    "delist_date",
    "cal_date",
    "ann_date",
    "actual_ann_date",
    "pre_date",
    "next_date",
}


def _is_yyyymmdd_int(value: Any) -> bool:
    """Return True if ``value`` looks like a YYYYMMDD int (19900101–20991231)."""
    if isinstance(value, (int,)) and not isinstance(value, bool):
        return 19900101 <= value <= 20991231
    return False


def df_to_tushare_payload(df: pd.DataFrame) -> dict[str, Any]:
    """Convert a DataFrame to a tushare Pro response payload."""
    if df is None or df.empty:
        return tushare_empty()

    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            # Datetime columns (e.g. realtime `trade_time`) keep wall-clock format.
            df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")
        elif col in _DATE_STRING_COLUMNS and pd.api.types.is_integer_dtype(df[col]):
            # Date columns stored as int YYYYMMDD must become "YYYYMMDD" strings.
            df[col] = df[col].astype(object).where(pd.notna(df[col]), None).map(
                lambda v: f"{int(v):08d}" if isinstance(v, (int,)) and not isinstance(v, bool) else v
            )
        else:
            df[col] = df[col].astype(object).where(pd.notna(df[col]), None)

    items = []
    for _, row in df.iterrows():
        items.append([_jsonify(v) for v in row.tolist()])

    return tushare_success(fields=df.columns.tolist(), items=items)


def _jsonify(value: Any) -> Any:
    """Make a single value JSON-friendly."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    import numpy as np
    from datetime import datetime

    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value) if not np.isnan(value) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y%m%d") if hasattr(value, "strftime") else str(value)
    return str(value)


# ---------------------------------------------------------------------------
# Parameter parsing
# ---------------------------------------------------------------------------


def parse_date_param(value: Any) -> Optional[int]:
    """Parse a date parameter to YYYYMMDD int."""
    if value is None:
        return None
    value = str(value).strip().replace("-", "")
    if not value:
        return None
    if not re.fullmatch(r"\d{8}", value):
        raise InvalidParameterError(f"Invalid date format: {value}, expected YYYYMMDD")
    return int(value)


def parse_int_param(value: Any, name: str) -> Optional[int]:
    """Parse an integer parameter."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError) as exc:
        raise InvalidParameterError(f"Invalid integer for {name}: {value}") from exc


def parse_code_param(value: Any) -> list[str]:
    """Parse a ts_code parameter into a list of codes."""
    if value is None:
        return []
    codes = [c.strip() for c in str(value).split(",") if c.strip()]
    if not codes:
        return []
    for code in codes:
        if "." not in code:
            raise InvalidParameterError(f"Invalid ts_code format: {code}, expected like 000001.SZ")
    return codes


def parse_fields_param(value: Any) -> Optional[list[str]]:
    """Parse a comma-separated fields parameter."""
    if value is None:
        return None
    fields = [f.strip() for f in str(value).split(",") if f.strip()]
    return fields or None


def filter_fields(df: pd.DataFrame, fields: Optional[Sequence[str]]) -> pd.DataFrame:
    """Filter DataFrame to only requested fields."""
    if fields is None or df is None or df.empty:
        return df
    available = [f for f in fields if f in df.columns]
    if not available:
        return df
    return df[available].copy()


# ---------------------------------------------------------------------------
# Request body helpers
# ---------------------------------------------------------------------------


async def parse_request_body(request: Request) -> dict[str, Any]:
    """Parse JSON or form body depending on content type."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return await request.json() or {}
        except Exception as exc:
            raise InvalidParameterError(f"Invalid JSON body: {exc}") from exc

    # Fallback to form/query params for GET/POST form requests
    return dict(request.query_params)


def extract_tushare_params(body: dict[str, Any]) -> tuple[str, dict[str, Any], Optional[list[str]], str]:
    """Extract api_name, params, fields and token from a tushare Pro request body."""
    api_name = body.get("api_name") or body.get("api")
    if not api_name:
        raise InvalidParameterError("api_name is required")

    params = body.get("params") or {}
    # Also allow top-level parameters for RESTful calls
    for key in ("ts_code", "start_date", "end_date", "trade_date", "cal_date",
                "exchange", "limit", "offset", "fields",
                "freq", "start_time", "end_time"):
        if key in body and key not in params:
            params[key] = body[key]

    fields = parse_fields_param(params.pop("fields", body.get("fields")))
    token = body.get("token", "")
    return str(api_name), params, fields, str(token)


# ---------------------------------------------------------------------------
# Exception to HTTP mapping
# ---------------------------------------------------------------------------


def handle_tushare_exception(exc: Exception) -> JSONResponse:
    """Map a domain exception to a tushare Pro error response.

    Per the Tushare Pro protocol the HTTP status is always 200 and the
    actual outcome is reported via the ``code`` field. Domain exceptions
    map to specific negative codes (mirroring the type's HTTP status for
    observability, e.g. ``InvalidParameterError`` → ``-400``).
    """
    if isinstance(exc, AdshareException):
        status = map_exception_to_http_status(exc)
        # Use a code derived from the HTTP status so 4xx/5xx surface in the
        # body, e.g. -400 for parameter errors, -500 for upstream issues.
        code = -status if isinstance(status, int) and 100 <= status < 1000 else -1
    else:
        code = -500
    msg = str(exc) or type(exc).__name__
    return JSONResponse(status_code=200, content=tushare_error(msg, code=code))
