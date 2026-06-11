"""Pro-style stock data API routers.

Provides endpoints matching the Pro data platform stock API conventions:
/stock_basic, /trade_cal, /daily, /weekly, /monthly,
/adj_factor, /pro_bar, /suspend_d
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from adshare.core.config import get_settings
from adshare.core.logging import get_logger
from adshare.historical.models import normalize_period
from adshare.historical.warehouse import get_warehouse
from adshare.services.dataframe_formatter import build_response, build_error_response, to_fields_items
from adshare.services.derived_metrics import (
    apply_adjustment,
    compute_moving_averages,
    compute_price_changes,
    convert_volume_to_lots,
    derive_suspensions,
    filter_fields,
    map_adj_factor_fields,
    map_kline_fields,
    map_stock_basic_fields,
    map_suspend_fields,
    map_trade_cal_fields,
)
from adshare.services.market_data import get_market_data_service

logger = get_logger(__name__)
router = APIRouter(tags=["stock-data"])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _parse_date_str(date_str: Optional[str]) -> Optional[int]:
    """Parse a YYYYMMDD string to int."""
    if not date_str:
        return None
    try:
        return int(date_str)
    except (ValueError, TypeError):
        return None


def _get_warehouse_or_none():
    """Return warehouse instance or None if disabled."""
    settings = get_settings()
    if not settings.historical_enabled:
        return None
    try:
        return get_warehouse(settings)
    except Exception as e:
        logger.warning("Warehouse init failed: %s", e)
        return None


def _codes_from_param(ts_code: Optional[str]) -> list[str]:
    """Split comma-separated TS codes."""
    if not ts_code:
        return []
    return [c.strip() for c in ts_code.split(",") if c.strip()]


# ------------------------------------------------------------------
# Stock Basic
# ------------------------------------------------------------------

@router.get("/stock_basic")
async def get_stock_basic(
    ts_code: Optional[str] = Query(default=None, description="TS code, e.g. 000001.SZ"),
    name: Optional[str] = Query(default=None, description="Stock name fuzzy match"),
    exchange: Optional[str] = Query(default=None, description="Exchange: SSE/SZSE/BSE"),
    market: Optional[str] = Query(default=None, description="Market type"),
    is_hs: Optional[str] = Query(default=None, description="HSC: N/H/S"),
    list_status: Optional[str] = Query(default=None, description="L/D/P"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
):
    """Get stock basic information."""
    try:
        warehouse = _get_warehouse_or_none()
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        # Query all codes from warehouse
        df = warehouse.query_codes()
        if df.empty:
            return build_response(data=to_fields_items(pd.DataFrame()))

        # Apply filters
        if ts_code:
            codes = _codes_from_param(ts_code)
            if "code" in df.columns:
                df = df[df["code"].isin(codes)]

        if name and "name" in df.columns:
            df = df[df["name"].astype(str).str.contains(name, na=False)]

        if market and "board" in df.columns:
            df = df[df["board"].astype(str) == market]

        if list_status and "is_listed" in df.columns:
            want_listed = list_status == "L"
            df = df[df["is_listed"] == want_listed]

        if exchange and "code" in df.columns:
            suffix_map = {"SSE": ".SH", "SZSE": ".SZ", "BSE": ".BJ"}
            want_suffix = suffix_map.get(exchange.upper(), "")
            if want_suffix:
                df = df[df["code"].astype(str).str.endswith(want_suffix)]

        # Map to Pro platform fields
        df = map_stock_basic_fields(df)

        # Filter requested fields
        df = filter_fields(df, fields)

        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("stock_basic failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Trade Calendar
# ------------------------------------------------------------------

@router.get("/trade_cal")
async def get_trade_cal(
    exchange: Optional[str] = Query(default=None, description="Exchange: SSE/SZSE/BSE"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    is_open: Optional[str] = Query(default=None, description="1=open, 0=closed"),
):
    """Get trading calendar."""
    try:
        warehouse = _get_warehouse_or_none()
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        # Map exchange to market code
        market = None
        if exchange:
            exchange_map = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}
            market = exchange_map.get(exchange.upper(), exchange.upper())

        df = warehouse.query_calendar(
            market=market,
            begin_date=_parse_date_str(start_date),
            end_date=_parse_date_str(end_date),
        )
        if df.empty:
            return build_response(data=to_fields_items(pd.DataFrame()))

        # Map to Pro platform fields
        df = map_trade_cal_fields(df)

        # Filter by is_open if requested
        if is_open is not None:
            want_open = int(is_open) == 1
            df = df[df["is_open"] == (1 if want_open else 0)]

        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("trade_cal failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Daily / Weekly / Monthly
# ------------------------------------------------------------------

def _get_kline_data(
    ts_code: Optional[str],
    trade_date: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    period: str,
    fields: Optional[str],
) -> dict:
    """Shared logic for daily/weekly/monthly endpoints."""
    codes = _codes_from_param(ts_code)
    if not codes:
        return build_error_response("ts_code is required")

    # Resolve date range
    td = _parse_date_str(trade_date)
    sd = _parse_date_str(start_date)
    ed = _parse_date_str(end_date)

    if td is not None:
        sd = td
        ed = td
    elif sd is None and ed is None:
        # No dates provided — use a wide range (last 365 days default)
        from datetime import datetime, timedelta
        ed = int(datetime.now().strftime("%Y%m%d"))
        sd = int((datetime.now() - timedelta(days=365)).strftime("%Y%m%d"))
    elif sd is None:
        sd = 19900101
    elif ed is None:
        from datetime import datetime
        ed = int(datetime.now().strftime("%Y%m%d"))

    service = get_market_data_service()
    result = service.get_kline(
        codes=codes,
        begin_date=sd,
        end_date=ed,
        period=period,
        source="auto",
    )
    df = result.df
    if df is None or df.empty:
        return build_response(data=to_fields_items(pd.DataFrame()))

    # Compute derived fields
    df = compute_price_changes(df)
    df = convert_volume_to_lots(df)
    df = map_kline_fields(df)
    df = filter_fields(df, fields)

    return build_response(data=to_fields_items(df))


@router.get("/daily")
async def get_daily(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
):
    """Get daily K-line data."""
    try:
        return _get_kline_data(ts_code, trade_date, start_date, end_date, "day", fields)
    except Exception as e:
        logger.error("daily failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/weekly")
async def get_weekly(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
):
    """Get weekly K-line data."""
    try:
        return _get_kline_data(ts_code, trade_date, start_date, end_date, "week", fields)
    except Exception as e:
        logger.error("weekly failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/monthly")
async def get_monthly(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
):
    """Get monthly K-line data."""
    try:
        return _get_kline_data(ts_code, trade_date, start_date, end_date, "month", fields)
    except Exception as e:
        logger.error("monthly failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Adj Factor
# ------------------------------------------------------------------

@router.get("/adj_factor")
async def get_adj_factor(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
):
    """Get adjustment factor data."""
    try:
        codes = _codes_from_param(ts_code)
        if not codes:
            return build_error_response("ts_code is required")

        td = _parse_date_str(trade_date)
        sd = _parse_date_str(start_date)
        ed = _parse_date_str(end_date)

        if td is not None:
            sd = td
            ed = td
        elif sd is None and ed is None:
            from datetime import datetime, timedelta
            ed = int(datetime.now().strftime("%Y%m%d"))
            sd = int((datetime.now() - timedelta(days=365)).strftime("%Y%m%d"))
        elif sd is None:
            sd = 19900101
        elif ed is None:
            from datetime import datetime
            ed = int(datetime.now().strftime("%Y%m%d"))

        warehouse = _get_warehouse_or_none()
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = warehouse.query_kline(codes=codes, begin_date=sd, end_date=ed, period="day")
        if df.empty:
            return build_response(data=to_fields_items(pd.DataFrame()))

        # Keep only adj_factor columns
        df = df[["code", "date", "adj_factor"]]
        df = map_adj_factor_fields(df)
        df = filter_fields(df, fields)

        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("adj_factor failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Pro Bar (Universal Bar with adj + MA)
# ------------------------------------------------------------------

@router.get("/pro_bar")
async def get_pro_bar(
    ts_code: str = Query(..., description="TS code"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    asset: Optional[str] = Query(default="E", description="E=stock, I=index"),
    adj: Optional[str] = Query(default=None, description="None/qfq/hfq"),
    freq: Optional[str] = Query(default="D", description="D/W/M"),
    ma: Optional[str] = Query(default=None, description="Moving averages, e.g. 5,10,20"),
):
    """Get universal bar data with optional adjustment and moving averages."""
    try:
        codes = _codes_from_param(ts_code)
        if not codes:
            return build_error_response("ts_code is required")

        # Resolve period from freq
        freq_map = {"D": "day", "W": "week", "M": "month"}
        period = freq_map.get(freq.upper(), "day") if freq else "day"

        # Resolve date range
        sd = _parse_date_str(start_date)
        ed = _parse_date_str(end_date)
        if sd is None and ed is None:
            from datetime import datetime, timedelta
            ed = int(datetime.now().strftime("%Y%m%d"))
            sd = int((datetime.now() - timedelta(days=365)).strftime("%Y%m%d"))
        elif sd is None:
            sd = 19900101
        elif ed is None:
            from datetime import datetime
            ed = int(datetime.now().strftime("%Y%m%d"))

        service = get_market_data_service()
        result = service.get_kline(
            codes=codes,
            begin_date=sd,
            end_date=ed,
            period=period,
            source="auto",
        )
        df = result.df
        if df is None or df.empty:
            return build_response(data=to_fields_items(pd.DataFrame()))

        # Apply adjustment if requested
        if adj and adj.lower() in ("qfq", "hfq"):
            adj_result = service.get_kline(
                codes=codes,
                begin_date=sd,
                end_date=ed,
                period=period,
                source="auto",
            )
            adj_df = adj_result.df
            if adj_df is not None and not adj_df.empty and "adj_factor" in adj_df.columns:
                df = apply_adjustment(df, adj_df[["date", "adj_factor"]], adj.lower())

        # Compute price changes
        df = compute_price_changes(df)
        df = convert_volume_to_lots(df)

        # Compute moving averages if requested
        if ma:
            try:
                ma_params = [int(x.strip()) for x in ma.split(",") if x.strip().isdigit()]
                if ma_params:
                    df = compute_moving_averages(df, ma_params)
            except Exception as e:
                logger.warning("MA calculation failed: %s", e)

        df = map_kline_fields(df)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("pro_bar failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Suspend
# ------------------------------------------------------------------

@router.get("/suspend_d")
async def get_suspend_d(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
):
    """Get suspension records derived from K-line data."""
    try:
        codes = _codes_from_param(ts_code)
        if not codes:
            return build_error_response("ts_code is required")

        td = _parse_date_str(trade_date)
        sd = _parse_date_str(start_date)
        ed = _parse_date_str(end_date)

        if td is not None:
            sd = td
            ed = td
        elif sd is None and ed is None:
            from datetime import datetime, timedelta
            ed = int(datetime.now().strftime("%Y%m%d"))
            sd = int((datetime.now() - timedelta(days=365 * 3)).strftime("%Y%m%d"))
        elif sd is None:
            sd = 19900101
        elif ed is None:
            from datetime import datetime
            ed = int(datetime.now().strftime("%Y%m%d"))

        warehouse = _get_warehouse_or_none()
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = warehouse.query_kline(codes=codes, begin_date=sd, end_date=ed, period="day")
        if df.empty:
            return build_response(data=to_fields_items(pd.DataFrame()))

        df = derive_suspensions(df)
        df = map_suspend_fields(df)
        df = filter_fields(df, fields)

        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("suspend_d failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
