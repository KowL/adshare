"""Map realtime Redis payloads to tushare Pro ``rt_k`` / ``rt_min`` rows.

The worker (:mod:`amazingdata.realtime`) serializes AmazingData SDK
snapshot / kline objects into plain dicts (``_serialize_data`` does a
full ``dir()`` dump) before writing them to Redis. This module converts
those dicts into the tushare Pro field naming convention:

* ``rt_k``   — Level-1 snapshot (最新价 + 5 档盘口 + 涨跌停价)
* ``rt_min`` — realtime minute kline bars

Field conversion failures degrade to ``None`` instead of raising, per
the design doc (§10 降级与边界).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

# tushare freq label -> internal kline period string
FREQ_TO_PERIOD = {
    "1MIN": "min1",
    "5MIN": "min5",
    "15MIN": "min15",
    "30MIN": "min30",
    "60MIN": "min60",
}
PERIOD_TO_FREQ = {v: k for k, v in FREQ_TO_PERIOD.items()}


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------


def quote_columns() -> list[str]:
    """tushare ``rt_k`` output field order."""
    cols = [
        "ts_code",
        "trade_time",
        "price",
        "open",
        "high",
        "low",
        "pre_close",
        "vol",
        "amount",
        "num_trades",
    ]
    for i in range(1, 6):
        cols += [f"b{i}_p", f"a{i}_p", f"b{i}_v", f"a{i}_v"]
    cols += ["high_limit", "low_limit"]
    return cols


def kline_columns() -> list[str]:
    """tushare ``rt_min`` output field order."""
    return [
        "ts_code",
        "trade_time",
        "freq",
        "open",
        "high",
        "low",
        "close",
        "vol",
        "amount",
    ]


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------


def quote_to_tushare_row(code: str, payload: dict) -> dict:
    """snapshot dict → tushare ``rt_k`` row."""
    payload = payload or {}
    row = {
        "ts_code": code or payload.get("code"),
        "trade_time": format_trade_time(payload.get("trade_time")),
        "price": payload.get("last"),
        "open": payload.get("open"),
        "high": payload.get("high"),
        "low": payload.get("low"),
        "pre_close": payload.get("pre_close"),
        "vol": payload.get("volume"),
        "amount": payload.get("amount"),
        "num_trades": payload.get("num_trades"),
        "high_limit": payload.get("high_limited"),
        "low_limit": payload.get("low_limited"),
    }
    for i in range(1, 6):
        row[f"b{i}_p"] = _pick(payload, f"bid_price{i}")
        row[f"a{i}_p"] = _pick(payload, f"ask_price{i}")
        # The SDK manual prints the volume fields as ``ask _volume1``
        # (with a space) — likely a doc defect since Python attributes
        # cannot contain spaces, but accept both spellings just in case.
        row[f"b{i}_v"] = _pick(payload, f"bid_volume{i}", f"bid _volume{i}")
        row[f"a{i}_v"] = _pick(payload, f"ask_volume{i}", f"ask _volume{i}")
    return row


def kline_to_tushare_row(code: str, freq: str, payload: dict) -> dict:
    """K 线 dict → tushare ``rt_min`` row."""
    payload = payload or {}
    return {
        "ts_code": code or payload.get("code"),
        "trade_time": format_trade_time(
            payload.get("kline_time") or payload.get("trade_time")
        ),
        "freq": freq,
        "open": payload.get("open"),
        "high": payload.get("high"),
        "low": payload.get("low"),
        "close": payload.get("close"),
        "vol": payload.get("volume"),
        "amount": payload.get("amount"),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def format_trade_time(value: Any) -> Optional[str]:
    """Normalize a trade_time-ish value to ``YYYY-MM-DD HH:MM:SS``.

    Accepts datetime objects, ISO strings and epoch milliseconds.
    Returns None on failure (never raises).
    """
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value / 1000)
        else:
            text = str(value).strip()
            if not text:
                return None
            if text.isdigit():
                dt = datetime.fromtimestamp(int(text) / 1000)
            else:
                dt = datetime.fromisoformat(text)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _pick(payload: dict, *keys: str) -> Any:
    """Return the first present, non-None value among ``keys``."""
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None
