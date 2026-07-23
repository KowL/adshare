"""Tushare Pro compatible stock data endpoints.

Routes under ``/tushare/stock/*`` return the tushare Pro response shape:
``{"code": 0, "msg": "", "data": {"fields": [...], "items": [...]}}``.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd
from fastapi import APIRouter, Depends, Request

from adshare import dependencies as deps
from adshare.core.exceptions import InvalidParameterError
from adshare.core.logging import get_logger
from adshare.routers.tushare.common import (
    df_to_tushare_payload,
    extract_tushare_params,
    filter_fields,
    handle_tushare_exception,
    parse_code_param,
    parse_date_param,
    parse_int_param,
    parse_request_body,
)
from adshare.services.derived_metrics import (
    aggregate_kline_period,
    build_limit_list,
    compute_price_changes,
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
router = APIRouter(prefix="/stock", tags=["tushare-stock"])


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
    df = map_kline_fields(df)
    return df


async def _extract_from_request(request: Request, api_name: str) -> tuple[dict[str, Any], Optional[list[str]]]:
    """Parse request body and return (params, fields)."""
    body = await parse_request_body(request)
    _, params, fields, _ = extract_tushare_params({**body, "api_name": api_name})
    return params, fields


# ---------------------------------------------------------------------------
# Core handlers (used by both RESTful routes and the unified /tushare entry)
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


# ---------------------------------------------------------------------------
# RESTful route wrappers
# ---------------------------------------------------------------------------


@router.post("/daily")
@router.get("/daily")
async def tushare_daily(
    request: Request,
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Tushare Pro ``daily`` endpoint."""
    try:
        params, fields = await _extract_from_request(request, "daily")
        return handle_daily(params, fields, service)
    except Exception as exc:
        return handle_tushare_exception(exc)


@router.post("/weekly")
@router.get("/weekly")
async def tushare_weekly(
    request: Request,
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Tushare Pro ``weekly`` endpoint."""
    try:
        params, fields = await _extract_from_request(request, "weekly")
        return handle_weekly(params, fields, service)
    except Exception as exc:
        return handle_tushare_exception(exc)


@router.post("/monthly")
@router.get("/monthly")
async def tushare_monthly(
    request: Request,
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Tushare Pro ``monthly`` endpoint."""
    try:
        params, fields = await _extract_from_request(request, "monthly")
        return handle_monthly(params, fields, service)
    except Exception as exc:
        return handle_tushare_exception(exc)


@router.post("/stock_basic")
@router.get("/stock_basic")
async def tushare_stock_basic(
    request: Request,
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Tushare Pro ``stock_basic`` endpoint."""
    try:
        params, fields = await _extract_from_request(request, "stock_basic")
        return handle_stock_basic(params, fields, service)
    except Exception as exc:
        return handle_tushare_exception(exc)


@router.post("/trade_cal")
@router.get("/trade_cal")
async def tushare_trade_cal(
    request: Request,
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Tushare Pro ``trade_cal`` endpoint."""
    try:
        params, fields = await _extract_from_request(request, "trade_cal")
        return handle_trade_cal(params, fields, service)
    except Exception as exc:
        return handle_tushare_exception(exc)


@router.post("/adj_factor")
@router.get("/adj_factor")
async def tushare_adj_factor(
    request: Request,
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Tushare Pro ``adj_factor`` endpoint."""
    try:
        params, fields = await _extract_from_request(request, "adj_factor")
        return handle_adj_factor(params, fields, service)
    except Exception as exc:
        return handle_tushare_exception(exc)


@router.post("/suspend_d")
@router.get("/suspend_d")
async def tushare_suspend_d(
    request: Request,
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Tushare Pro ``suspend_d`` endpoint."""
    try:
        params, fields = await _extract_from_request(request, "suspend_d")
        return handle_suspend_d(params, fields, service)
    except Exception as exc:
        return handle_tushare_exception(exc)


@router.post("/limit_list")
@router.get("/limit_list")
async def tushare_limit_list(
    request: Request,
    up_service: LimitUpService = Depends(deps.get_limit_up_service_dep),
    down_service: LimitDownService = Depends(deps.get_limit_down_service_dep),
):
    """Tushare Pro ``limit_list`` endpoint."""
    try:
        params, fields = await _extract_from_request(request, "limit_list")
        return handle_limit_list(params, fields, up_service, down_service)
    except Exception as exc:
        return handle_tushare_exception(exc)


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
}
