"""Market data routers."""

from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from adshare.core.exceptions import AdshareException, map_exception_to_http_status
from adshare.core.logging import get_logger
from adshare import dependencies as deps
from adshare.models.schemas import (
    CalendarResponse,
    CodeListResponse,
    KlineResponse,
    LimitDownResponse,
    LimitUpLadderResponse,
    LimitUpResponse,
    MarketActivityResponse,
    SnapshotResponse,
    StockBasicResponse,
    StockBasicSummary,
    StrongStockPoolResponse,
)
from adshare.services.limit_up import (
    LimitDownService,
    LimitUpService,
    MarketActivityService,
    StrongStockPoolService,
)
from adshare.services.mappers import dataframe_to_kline_items, dataframe_to_snapshot_items
from adshare.services.market_data import MarketDataService

logger = get_logger(__name__)
router = APIRouter(prefix="/market", tags=["market"])


def _handle_exception(exc: Exception) -> HTTPException:
    """Map domain exceptions to HTTP exceptions."""
    if isinstance(exc, AdshareException):
        status = map_exception_to_http_status(exc)
        return HTTPException(status_code=status, detail=str(exc))
    logger.error("Unhandled exception: %s", exc)
    return HTTPException(status_code=500, detail=str(exc) or "Internal server error")


@router.get("/codes", response_model=CodeListResponse)
async def get_code_list(
    security_type: str = Query(
        default="stock_a",
        description="Security type: stock_a, index_a, etf, ... "
        "(legacy EXTRA_* values are still accepted)",
    ),
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Get security code list."""
    try:
        codes = service.get_code_list(security_type=security_type)
        return CodeListResponse(
            security_type=security_type,
            code_list=codes,
            count=len(codes),
            data=codes,
        )
    except Exception as e:
        raise _handle_exception(e) from e


@router.get("/calendar", response_model=CalendarResponse)
async def get_calendar(
    market: str = Query(default="SH", description="Market code"),
    date: Optional[int] = Query(default=None, description="Query date YYYYMMDD"),
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Get trading calendar."""
    try:
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
        raise _handle_exception(e) from e


@router.get("/kline", response_model=KlineResponse)
async def get_kline(
    codes: str = Query(..., description="Comma-separated stock codes"),
    begin_date: int = Query(..., description="Start date YYYYMMDD"),
    end_date: int = Query(..., description="End date YYYYMMDD"),
    period: str = Query(default="day", description="Period: day, week, month, min1, min5"),
    limit: Optional[int] = Query(default=None, description="Max records"),
    offset: int = Query(default=0, description="Records to skip"),
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Get K-line data.

    Reads from L3 historical warehouse (Parquet/DuckDB).
    SDK fallback is disabled in API-only mode.
    """
    try:
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
        raise _handle_exception(e) from e


@router.get("/kline/simple", response_model=KlineResponse)
async def get_kline_simple(
    symbol: str = Query(..., description="Stock code, e.g. 002654.SZ"),
    count: int = Query(default=60, description="Number of bars to fetch"),
    period: str = Query(default="day", description="Period: day, week, month"),
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
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
        raise _handle_exception(e) from e


@router.get("/snapshot", response_model=SnapshotResponse)
async def get_snapshot(
    codes: str = Query(..., description="Comma-separated stock codes"),
    date: Optional[int] = Query(default=None, description="Trade date YYYYMMDD"),
    time: Optional[int] = Query(default=None, description="Trade time HHMMSS"),
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Get snapshot data."""
    try:
        df = service.get_snapshot(codes=codes, date=date, time=time)
        items = dataframe_to_snapshot_items(df)

        return SnapshotResponse(count=len(items), data=items)
    except Exception as e:
        raise _handle_exception(e) from e


@router.get("/stock/basic", response_model=StockBasicResponse)
async def get_stock_basic(
    codes: Optional[str] = Query(default=None, description="Comma-separated codes, empty for all"),
    summary_only: bool = Query(default=False, description="Return summary only"),
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Get stock basic information."""
    try:
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
        raise _handle_exception(e) from e


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
    service: LimitUpService = Depends(deps.get_limit_up_service_dep),
):
    """Get limit-up stocks for recent trading days."""
    try:
        return service.get_limit_up(
            days=days,
            date=date,
            board_filter=board_filter,
            exclude_st=exclude_st,
        )
    except Exception as e:
        raise _handle_exception(e) from e


@router.get("/limit-up/ladder", response_model=LimitUpLadderResponse)
async def get_limit_up_ladder(
    days: int = Query(default=15, description="Number of trading days to look back"),
    date: Optional[int] = Query(default=None, description="Trade date YYYYMMDD"),
    board_filter: Optional[str] = Query(default=None, description="Filter by board: 主板, 创业板, 科创板, 北交所"),
    exclude_st: bool = Query(default=True, description="Exclude ST/*ST stocks"),
    service: LimitUpService = Depends(deps.get_limit_up_service_dep),
):
    """Get limit-up ladder (consecutive limit-up levels)."""
    try:
        return service.get_ladder(
            days=days,
            date=date,
            board_filter=board_filter,
            exclude_st=exclude_st,
        )
    except Exception as e:
        raise _handle_exception(e) from e


# ============================================================
# Limit-Down Data
# ============================================================


@router.get("/limit-down", response_model=LimitDownResponse)
async def get_limit_down(
    days: int = Query(default=1, description="Number of trading days to look back"),
    date: Optional[int] = Query(default=None, description="Trade date YYYYMMDD"),
    board_filter: Optional[str] = Query(
        default=None,
        description="Filter by board: 主板, 创业板, 科创板, 北交所",
    ),
    exclude_st: bool = Query(default=True, description="Exclude ST/*ST stocks"),
    service: LimitDownService = Depends(deps.get_limit_down_service_dep),
):
    """Get limit-down stocks for recent trading days."""
    try:
        return service.get_limit_down(
            days=days,
            date=date,
            board_filter=board_filter,
            exclude_st=exclude_st,
        )
    except Exception as e:
        raise _handle_exception(e) from e


# ============================================================
# Market Activity (赚钱效应)
# ============================================================


@router.get("/market-activity", response_model=MarketActivityResponse)
async def get_market_activity(
    date: Optional[int] = Query(default=None, description="Trade date YYYYMMDD"),
    service: MarketActivityService = Depends(deps.get_market_activity_service_dep),
):
    """Get market activity / 赚钱效应 for a trading day."""
    try:
        return service.get_market_activity(date=date)
    except Exception as e:
        raise _handle_exception(e) from e


# ============================================================
# Strong Stock Pool (强势股池)
# ============================================================


@router.get("/strong-pool", response_model=StrongStockPoolResponse)
async def get_strong_pool(
    date: Optional[int] = Query(default=None, description="Trade date YYYYMMDD"),
    lookback_days: int = Query(default=20, description="Lookback window for new-high and limit-up count"),
    min_change_pct: float = Query(default=0.03, description="Minimum change pct to be considered strong"),
    service: StrongStockPoolService = Depends(deps.get_strong_stock_pool_service_dep),
):
    """Get strong stock pool for a trading day."""
    try:
        return service.get_strong_pool(
            date=date,
            lookback_days=lookback_days,
            min_change_pct=min_change_pct,
        )
    except Exception as e:
        raise _handle_exception(e) from e
