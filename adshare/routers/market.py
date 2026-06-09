"""Market data routers."""

from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from adshare.core.logging import get_logger
from adshare.models.schemas import (
    CalendarResponse,
    CodeListResponse,
    KlineResponse,
    LimitUpLadderResponse,
    LimitUpResponse,
    SnapshotResponse,
    StockBasicResponse,
    StockBasicSummary,
)
from adshare.services.limit_up import get_limit_up_service
from adshare.services.mappers import dataframe_to_kline_items, dataframe_to_snapshot_items
from adshare.services.market_data import get_market_data_service

logger = get_logger(__name__)
router = APIRouter(prefix="/market", tags=["market"])


@router.get("/codes", response_model=CodeListResponse)
async def get_code_list(
    security_type: str = Query(default="EXTRA_STOCK_A", description="Security type")
):
    """Get security code list."""
    try:
        service = get_market_data_service()
        codes = service.get_code_list(security_type=security_type)
        return CodeListResponse(
            security_type=security_type,
            code_list=codes,
            count=len(codes),
            data=codes,
        )
    except Exception as e:
        logger.error(f"get_code_list failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/calendar", response_model=CalendarResponse)
async def get_calendar(
    market: str = Query(default="SH", description="Market code"),
    date: Optional[int] = Query(default=None, description="Query date YYYYMMDD"),
):
    """Get trading calendar."""
    try:
        service = get_market_data_service()
        df = service.get_calendar(market=market, date=date)
        calendar = df["date"].tolist() if "date" in df.columns else []
        return CalendarResponse(
            market=market,
            query_date=date or 0,
            calendar=calendar,
            count=len(calendar),
            data=calendar,
        )
    except Exception as e:
        logger.error(f"get_calendar failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/kline", response_model=KlineResponse)
async def get_kline(
    codes: str = Query(..., description="Comma-separated stock codes"),
    begin_date: int = Query(..., description="Start date YYYYMMDD"),
    end_date: int = Query(..., description="End date YYYYMMDD"),
    period: str = Query(default="day", description="Period: day, week, month, min1, min5"),
    limit: Optional[int] = Query(default=None, description="Max records"),
    offset: int = Query(default=0, description="Records to skip"),
):
    """Get K-line data.

    Lookup order: L3 historical warehouse -> SDK.
    """
    try:
        service = get_market_data_service()
        result = service.get_kline(
            codes=codes,
            begin_date=begin_date,
            end_date=end_date,
            period=period,
            limit=limit,
            offset=offset,
            source="auto",
        )
        df = result.df

        items = dataframe_to_kline_items(df)

        return KlineResponse(
            codes=codes.split(","),
            period=period,
            begin_date=begin_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
            count=len(items),
            data=items,
        )
    except Exception as e:
        logger.error(f"get_kline failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/kline/simple", response_model=KlineResponse)
async def get_kline_simple(
    symbol: str = Query(..., description="Stock code, e.g. 002654.SZ"),
    count: int = Query(default=60, description="Number of bars to fetch"),
    period: str = Query(default="day", description="Period: day, week, month"),
):
    """Simplified K-line endpoint: auto-calculate date range from count.
    
    Example: /market/kline/simple?symbol=002654.SZ&count=60&period=day
    """
    from datetime import datetime, timedelta
    
    try:
        # Calculate date range
        end_dt = datetime.now()
        # Rough estimate: count days + weekends + holidays buffer
        start_dt = end_dt - timedelta(days=int(count * 1.5) + 30)
        
        begin_date = int(start_dt.strftime("%Y%m%d"))
        end_date = int(end_dt.strftime("%Y%m%d"))
        
        service = get_market_data_service()
        result = service.get_kline(
            codes=symbol,
            begin_date=begin_date,
            end_date=end_date,
            period=period,
            limit=count,
            offset=0,
            source="auto",
        )
        df = result.df

        items = dataframe_to_kline_items(df)

        return KlineResponse(
            codes=[symbol],
            period=period,
            begin_date=begin_date,
            end_date=end_date,
            limit=count,
            offset=0,
            count=len(items),
            data=items,
        )
    except Exception as e:
        logger.error(f"get_kline_simple failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/snapshot", response_model=SnapshotResponse)
async def get_snapshot(
    codes: str = Query(..., description="Comma-separated stock codes"),
    date: Optional[int] = Query(default=None, description="Trade date YYYYMMDD"),
    time: Optional[int] = Query(default=None, description="Trade time HHMMSS"),
):
    """Get snapshot data."""
    try:
        service = get_market_data_service()
        df = service.get_snapshot(codes=codes, date=date, time=time)
        items = dataframe_to_snapshot_items(df)

        return SnapshotResponse(count=len(items), data=items)
    except Exception as e:
        logger.error(f"get_snapshot failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stock/basic", response_model=StockBasicResponse)
async def get_stock_basic(
    codes: Optional[str] = Query(default=None, description="Comma-separated codes, empty for all"),
    summary_only: bool = Query(default=False, description="Return summary only"),
):
    """Get stock basic information."""
    try:
        service = get_market_data_service()
        df = service.get_stock_basic(codes=codes, summary_only=summary_only)

        if summary_only:
            summary = StockBasicSummary(
                total_count=int(df.get("total_count", 0)),
                listed_count=int(df.get("listed_count", 0)),
                delisted_count=int(df.get("delisted_count", 0)),
                markets=df.get("markets", []),
            )
            return StockBasicResponse(summary=summary, count=0, data=[])

        items = []
        for _, row in df.iterrows():
            ld = row.get("list_date")
            dd = row.get("delist_date")
            il = row.get("is_listed")
            items.append(
                {
                    "code": str(row.get("code", "")),
                    "name": str(row.get("name", "")),
                    "comp_name": str(row.get("comp_name", "")),
                    "list_date": int(ld) if pd.notna(ld) and str(ld).isdigit() else None,
                    "delist_date": int(dd) if pd.notna(dd) and str(dd).isdigit() else None,
                    "list_plate": str(row.get("list_plate", "")),
                    "is_listed": int(il) if pd.notna(il) else None,
                }
            )

        return StockBasicResponse(count=len(items), data=items)
    except Exception as e:
        logger.error(f"get_stock_basic failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Limit-Up Data
# ============================================================


@router.get("/limit-up", response_model=LimitUpResponse)
async def get_limit_up(
    days: int = Query(default=1, description="Number of trading days to look back"),
    date: Optional[int] = Query(default=None, description="Trade date YYYYMMDD"),
    board_filter: Optional[str] = Query(
        default=None,
        description="Filter by board: 主板, 创业板, 科创板, 北交所",
    ),
    exclude_st: bool = Query(default=True, description="Exclude ST/*ST stocks"),
):
    """Get limit-up stocks for recent trading days.
    
    This endpoint calculates limit-up stocks from daily K-line data.
    Since AmazingData SDK doesn't provide native limit-up data,
    we compute the theoretical limit-up price from the previous close and board rate.
    """
    try:
        return get_limit_up_service().get_limit_up(
            days=days,
            date=date,
            board_filter=board_filter,
            exclude_st=exclude_st,
        )
    except Exception as e:
        logger.error(f"get_limit_up failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/limit-up/ladder", response_model=LimitUpLadderResponse)
async def get_limit_up_ladder(
    days: int = Query(default=15, description="Number of trading days to look back"),
    date: Optional[int] = Query(default=None, description="Trade date YYYYMMDD"),
    board_filter: Optional[str] = Query(default=None, description="Filter by board"),
    exclude_st: bool = Query(default=True, description="Exclude ST/*ST stocks"),
):
    """Get limit-up ladder (consecutive limit-up levels).
    
    Note: Since the current implementation uses single-day K-line data,
    limitUpDays is always 1.
    For true consecutive days calculation, historical K-line data would be needed.
    """
    try:
        return get_limit_up_service().get_ladder(
            days=days,
            date=date,
            board_filter=board_filter,
            exclude_st=exclude_st,
        )
    except Exception as e:
        logger.error(f"get_limit_up_ladder failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
