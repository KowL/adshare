"""Tushare Pro compatible stock data handlers.

Handlers are invoked by the unified ``POST /tushare`` entry point via the
``HANDLERS`` registry. Each handler returns the tushare Pro response shape:
``{"code": 0, "msg": "", "data": {"fields": [...], "items": [...]}}``.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from adshare.core.exceptions import InvalidParameterError
from adshare.core.logging import get_logger
from adshare.routers.tushare.common import (
    df_to_tushare_payload,
    filter_fields,
    parse_code_param,
    parse_date_param,
    parse_int_param,
)
from adshare.services.derived_metrics import (
    aggregate_kline_period,
    build_limit_list,
    build_limit_list_d,
    compute_price_changes,
    convert_amount_to_thousands,
    convert_volume_to_lots,
    derive_suspensions,
    map_adj_factor_fields,
    map_kline_fields,
    map_stock_basic_fields,
    map_suspend_fields,
    map_trade_cal_fields,
    kline_lookback_date,
)
from adshare.services.limit_up import LimitDownService, LimitUpService
from adshare.services.market_data import MarketDataService

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_kline_date(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize AmazingData kline_time/date column to int YYYYMMDD."""
    if df is None or df.empty:
        return df
    df = df.copy()
    if "kline_time" in df.columns and "date" not in df.columns:
        df = df.rename(columns={"kline_time": "date"})
    if "date" in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df["date"]):
            df["date"] = df["date"].dt.strftime("%Y%m%d").astype(int)
        else:
            df["date"] = pd.to_numeric(df["date"], errors="coerce").fillna(0).astype(int)
    return df


def _fetch_kline(
    service: MarketDataService,
    codes: list[str],
    start_date: int,
    end_date: int,
    period: str,
    limit: Optional[int] = None,
    offset: int = 0,
) -> pd.DataFrame:
    """Fetch kline data from the warehouse."""
    if not codes:
        raise InvalidParameterError("ts_code is required")

    query_period = "day" if period in {"week", "month"} else period
    result = service.get_kline(
        codes=codes,
        begin_date=kline_lookback_date(start_date),
        end_date=end_date,
        period=query_period,
    )
    df = result.df
    if df is None or df.empty:
        return pd.DataFrame()

    df = _normalize_kline_date(df)
    df = aggregate_kline_period(df, period)
    df = compute_price_changes(df)
    if "date" in df.columns:
        dates = pd.to_numeric(df["date"], errors="coerce")
        df = df[(dates >= start_date) & (dates <= end_date)]
    if offset:
        df = df.iloc[offset:]
    if limit is not None:
        df = df.iloc[:limit]
    df = convert_volume_to_lots(df)
    df = convert_amount_to_thousands(df)
    df = map_kline_fields(df)
    if "trade_date" in df.columns:
        df = df.sort_values("trade_date").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Core handlers (invoked by the unified /tushare entry point)
# ---------------------------------------------------------------------------


def handle_daily(
    params: dict[str, Any], fields: Optional[list[str]], service: MarketDataService, **kwargs
) -> dict[str, Any]:
    codes = parse_code_param(params.get("ts_code"))
    start_date = parse_date_param(params.get("start_date")) or 19900101
    end_date = parse_date_param(params.get("end_date")) or 20991231
    limit = parse_int_param(params.get("limit"), "limit")
    offset = parse_int_param(params.get("offset"), "offset") or 0

    df = _fetch_kline(service, codes, start_date, end_date, "day", limit, offset)
    df = filter_fields(df, fields)
    return df_to_tushare_payload(df)


def handle_weekly(
    params: dict[str, Any], fields: Optional[list[str]], service: MarketDataService, **kwargs
) -> dict[str, Any]:
    codes = parse_code_param(params.get("ts_code"))
    start_date = parse_date_param(params.get("start_date")) or 19900101
    end_date = parse_date_param(params.get("end_date")) or 20991231
    limit = parse_int_param(params.get("limit"), "limit")
    offset = parse_int_param(params.get("offset"), "offset") or 0

    df = _fetch_kline(service, codes, start_date, end_date, "week", limit, offset)
    df = filter_fields(df, fields)
    return df_to_tushare_payload(df)


def handle_monthly(
    params: dict[str, Any], fields: Optional[list[str]], service: MarketDataService, **kwargs
) -> dict[str, Any]:
    codes = parse_code_param(params.get("ts_code"))
    start_date = parse_date_param(params.get("start_date")) or 19900101
    end_date = parse_date_param(params.get("end_date")) or 20991231
    limit = parse_int_param(params.get("limit"), "limit")
    offset = parse_int_param(params.get("offset"), "offset") or 0

    df = _fetch_kline(service, codes, start_date, end_date, "month", limit, offset)
    df = filter_fields(df, fields)
    return df_to_tushare_payload(df)


def handle_stock_basic(
    params: dict[str, Any], fields: Optional[list[str]], service: MarketDataService, **kwargs
) -> dict[str, Any]:
    ts_code = params.get("ts_code")
    codes = parse_code_param(ts_code) if ts_code else None

    df = service.get_stock_basic(codes=",".join(codes) if codes else None)
    if df is None or df.empty:
        return df_to_tushare_payload(pd.DataFrame())

    df = map_stock_basic_fields(df)
    df = filter_fields(df, fields)
    return df_to_tushare_payload(df)


def handle_trade_cal(
    params: dict[str, Any], fields: Optional[list[str]], service: MarketDataService, **kwargs
) -> dict[str, Any]:
    exchange = params.get("exchange", "SSE")
    market_map = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}
    market = market_map.get(str(exchange).upper(), str(exchange).upper())

    df = service.get_calendar(market=market)
    if df is None or df.empty:
        return df_to_tushare_payload(pd.DataFrame())

    start_date = parse_date_param(params.get("start_date"))
    end_date = parse_date_param(params.get("end_date"))
    if start_date is not None and "date" in df.columns:
        df = df[pd.to_numeric(df["date"], errors="coerce").fillna(0).astype(int) >= start_date]
    if end_date is not None and "date" in df.columns:
        df = df[pd.to_numeric(df["date"], errors="coerce").fillna(0).astype(int) <= end_date]

    df = map_trade_cal_fields(df)
    df = filter_fields(df, fields)
    return df_to_tushare_payload(df)


def handle_adj_factor(
    params: dict[str, Any], fields: Optional[list[str]], service: MarketDataService, **kwargs
) -> dict[str, Any]:
    codes = parse_code_param(params.get("ts_code"))
    start_date = parse_date_param(params.get("start_date")) or 19900101
    end_date = parse_date_param(params.get("end_date")) or 20991231

    if not codes:
        raise InvalidParameterError("ts_code is required")

    df = service.get_adjustment_factors(
        codes=codes,
        begin_date=start_date,
        end_date=end_date,
    )
    if df is None or df.empty:
        return df_to_tushare_payload(pd.DataFrame())

    df = _normalize_kline_date(df)
    df = map_adj_factor_fields(df)
    df = filter_fields(df, fields)
    return df_to_tushare_payload(df)


def handle_suspend_d(
    params: dict[str, Any], fields: Optional[list[str]], service: MarketDataService, **kwargs
) -> dict[str, Any]:
    codes = parse_code_param(params.get("ts_code"))
    start_date = parse_date_param(params.get("start_date")) or 19900101
    end_date = parse_date_param(params.get("end_date")) or 20991231

    if not codes:
        raise InvalidParameterError("ts_code is required")

    result = service.get_kline(
        codes=codes,
        begin_date=start_date,
        end_date=end_date,
        period="day",
    )
    df = result.df
    if df is None or df.empty:
        return df_to_tushare_payload(pd.DataFrame())

    df = _normalize_kline_date(df)
    df = derive_suspensions(df)
    df = map_suspend_fields(df)
    df = filter_fields(df, fields)
    return df_to_tushare_payload(df)


def handle_limit_list(
    params: dict[str, Any],
    fields: Optional[list[str]],
    up_service: LimitUpService,
    down_service: LimitDownService,
    **kwargs,
) -> dict[str, Any]:
    trade_date = parse_date_param(params.get("trade_date"))
    if trade_date is None:
        from adshare.services.limit_up import _today_int

        trade_date = _today_int()

    up_response = up_service.get_limit_up(date=trade_date)
    down_response = down_service.get_limit_down(date=trade_date)

    df = build_limit_list(up_response.stocks, down_response.stocks, trade_date)
    df = filter_fields(df, fields)
    return df_to_tushare_payload(df)


def _ts_code_matches(item, ts_code: Optional[str]) -> bool:
    """Return True if the item's code matches the ``ts_code`` filter.

    Accepts either a fully-qualified ``ts_code`` (``000001.SZ``) or a
    bare code (``000001``). The ``LimitUpItem.code`` field stores bare
    codes, so we compare against the suffix-stripped form.
    """
    if not ts_code:
        return True
    code = str(getattr(item, "code", ""))
    target = str(ts_code).strip()
    if "." in target:
        target_bare = target.split(".", 1)[0]
    else:
        target_bare = target
    return code == target or code == target_bare


def _exchange_matches(item, exchange: Optional[str]) -> bool:
    """Return True if the item's code is on the requested exchange."""
    if not exchange:
        return True
    code = str(getattr(item, "code", ""))
    bare = code.split(".", 1)[0] if "." in code else code
    e = str(exchange).strip().upper()
    if e in {"SH", "SSE"}:
        return bare.startswith(("60", "68", "88", "89"))
    if e in {"SZ", "SZSE"}:
        return bare.startswith(("00", "30"))
    if e in {"BJ", "BSE"}:
        return bare.startswith(("8", "4", "92", "93"))
    return True


def handle_limit_list_d(
    params: dict[str, Any],
    fields: Optional[list[str]],
    up_service: LimitUpService,
    down_service: LimitDownService,
    **kwargs,
) -> dict[str, Any]:
    """Handler for the ``limit_list_d`` API.

    Spec: https://tushare.pro/document/2?doc_id=298

    Filters:
      * ``trade_date`` (YYYYMMDD; defaults to today)
      * ``ts_code`` (e.g. ``000001.SZ``)
      * ``limit_type`` (``U`` / ``D`` / ``Z``) — only the requested types
        are included; ``Z`` (炸板) requires additional computation that is
        currently beyond the warehouse snapshots.
      * ``exchange`` (``SH`` / ``SZ`` / ``BJ``)
    """
    from adshare.services.limit_up import _today_int

    trade_date = parse_date_param(params.get("trade_date")) or _today_int()
    ts_code = params.get("ts_code")
    limit_type = str(params.get("limit_type") or "").upper() or None
    exchange = params.get("exchange")

    up_stocks: list = []
    down_stocks: list = []

    if limit_type in (None, "U", "Z"):
        up_response = up_service.get_limit_up(date=trade_date)
        up_stocks = [
            item for item in up_response.stocks
            if _ts_code_matches(item, ts_code) and _exchange_matches(item, exchange)
        ]
    if limit_type in (None, "D"):
        down_response = down_service.get_limit_down(date=trade_date)
        down_stocks = [
            item for item in down_response.stocks
            if _ts_code_matches(item, ts_code) and _exchange_matches(item, exchange)
        ]

    if limit_type == "Z":
        # 炸板: stocks that *touched* the upper-limit price intraday but
        # failed to close there. The current limit-up pipeline only emits
        # stocks whose close hit the limit, so emit an explicit empty frame
        # rather than misleading values.
        up_stocks = []
        down_stocks = []

    df = build_limit_list_d(up_stocks, down_stocks, trade_date)
    df = filter_fields(df, fields)
    return df_to_tushare_payload(df)


# ---------------------------------------------------------------------------
# Handler registry for the unified /tushare entry point
# ---------------------------------------------------------------------------


HANDLERS: dict[str, Any] = {
    "daily": handle_daily,
    "weekly": handle_weekly,
    "monthly": handle_monthly,
    "stock_basic": handle_stock_basic,
    "trade_cal": handle_trade_cal,
    "adj_factor": handle_adj_factor,
    "suspend_d": handle_suspend_d,
    "limit_list": handle_limit_list,
    "limit_list_d": handle_limit_list_d,
}
