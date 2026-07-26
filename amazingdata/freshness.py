"""Publish per-period kline freshness to Redis.

Called from ``batch.py:_write_period_metadata`` after each successful
sync. Writes a JSON row to ``adshare:data:freshness:kline:{day|week|month}``
with TTL 7 days.

The ``is_in_progress`` flag distinguishes "this bar's date is today
but the bar hasn't closed" from "this bar is fully closed". The
dashboard renders the former amber, the latter green.

Same ``is_in_progress`` rule applies to week and month: the bar is
considered in progress if the period's last trading date is the most
recent trading day in the period that is >= today. Once the period
closes (e.g. Friday after close for week), ``is_in_progress`` becomes
``False`` on the next sync.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

_CST = ZoneInfo("Asia/Shanghai")
_FRESHNESS_TTL_SEC = 7 * 24 * 3600
_PERIOD_ALIASES = {
    "day": "day", "daily": "day",
    "week": "week", "weekly": "week",
    "month": "month", "monthly": "month",
}


def _int_to_date(yyyymmdd: int) -> date:
    s = f"{int(yyyymmdd):08d}"
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _load_calendar(historical_path: Path) -> List[date]:
    """Return sorted trading dates from ``data/meta/calendar.parquet``.

    Empty list on any failure — caller treats that as "don't compare
    against calendar" and falls back to a naive heuristic.
    """
    try:
        import pandas as pd
        path = historical_path / "meta" / "calendar.parquet"
        if not path.exists():
            return []
        df = pd.read_parquet(
            path, columns=["date", "is_trading_day"],
        )
        if df is None or df.empty:
            return []
        df = df[df["is_trading_day"] == True]  # noqa: E712 — parquet stores bool
        out: List[date] = []
        for v in df["date"].dropna().tolist():
            try:
                out.append(_int_to_date(int(v)))
            except (ValueError, TypeError):
                continue
        return sorted(out)
    except Exception:
        return []


def _last_of_period(
    period: str, target: date, calendar: List[date],
) -> Optional[date]:
    """Last trading date in the calendar that falls in the same ISO
    week (for ``week``) or calendar month (for ``month``) as ``target``."""
    if not calendar:
        return None
    if period == "week":
        week_key = target.isocalendar()[:2]
        in_period = [d for d in calendar if d.isocalendar()[:2] == week_key]
    elif period == "month":
        in_period = [
            d for d in calendar
            if (d.year, d.month) == (target.year, target.month)
        ]
    else:
        return None
    return in_period[-1] if in_period else None


def _is_in_progress(
    period: str, last_date: date, today: date, calendar: List[date],
) -> bool:
    """True iff ``last_date`` belongs to a period that has not yet closed.

    For ``day``: ``last_date == today`` and today is a trading day.
    For ``week``/``month``: the period's last trading day is
    ``>= today`` and ``last_date`` is that date (or later — which only
    happens if the calendar is missing future rows, treated as in
    progress to be safe).
    """
    if last_date > today:
        return False
    if period == "day":
        if calendar:
            return last_date >= today and any(
                d == today for d in calendar
            )
        return last_date == today
    last_of = _last_of_period(period, today, calendar) if calendar else None
    if last_of is None:
        return False
    return last_date >= last_of


def _previous_complete_date(
    period: str, last_date: date, calendar: List[date],
) -> Optional[date]:
    """For an in-progress bar, return the most recent *closed* trading
    date of the same kind of period; otherwise ``last_date`` itself."""
    if not calendar:
        return last_date
    earlier = [d for d in calendar if d < last_date]
    if not earlier:
        return None
    if period == "day":
        return earlier[-1]
    last_of = _last_of_period(period, earlier[-1], calendar)
    return last_of


def publish_kline_freshness(
    period: str,
    code_count: int,
    rows_inserted: int,
    first_date: Optional[int],
    last_date: Optional[int],
    last_sync_at: int,
    historical_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Publish freshness for one kline period. Returns the row that
    was written (or ``None`` if nothing was publishable).

    Called from :func:`amazingdata.batch._write_period_metadata`.
    """
    period_norm = _PERIOD_ALIASES.get(period)
    if period_norm is None or last_date is None:
        return None

    today = datetime.now(_CST).date()
    last_d = _int_to_date(int(last_date))

    root = Path(historical_path) if historical_path else Path("./data")
    calendar = _load_calendar(root)

    in_progress = _is_in_progress(period_norm, last_d, today, calendar)
    complete_d = (
        _previous_complete_date(period_norm, last_d, calendar)
        if in_progress
        else last_d
    )

    row: Dict[str, Any] = {
        "period": period_norm,
        "latest_trade_date": last_d.isoformat(),
        "latest_complete_date": (
            complete_d.isoformat() if complete_d is not None else None
        ),
        "code_count": int(code_count),
        "last_run_at": datetime.fromtimestamp(int(last_sync_at), _CST).isoformat(),
        "last_run_status": "ok",
        "last_error": None,
        "rows_inserted": int(rows_inserted),
        "is_in_progress": bool(in_progress),
    }

    key = f"adshare:data:freshness:kline:{period_norm}"
    try:
        from adshare.core.cache import get_cache_manager

        cache = get_cache_manager()
        cache.redis.set(
            key,
            json.dumps(row, ensure_ascii=False),
            ex=_FRESHNESS_TTL_SEC,
        )
        return row
    except Exception:
        return None