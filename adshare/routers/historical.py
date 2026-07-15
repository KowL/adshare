"""Public HTTP endpoints for the L3 historical data warehouse.

Exposes read access to the on-disk Parquet files via DuckDB. When
``HISTORICAL_ENABLED=false`` the endpoints return 503 with a clear
message; SDK fallback is intentionally opt-in to keep behaviour
predictable.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from adshare import dependencies as deps
from adshare.core.config import get_settings
from adshare.core.logging import get_logger
from adshare.historical.warehouse import HistoricalWarehouse, get_warehouse
from adshare.models.schemas import (
    HistoricalCalendarResponse,
    HistoricalCodesResponse,
    HistoricalKlineResponse,
    HistoricalSqlRequest,
    HistoricalSqlResponse,
)
from adshare.services.mappers import dataframe_to_historical_kline_records, dataframe_to_json_rows
from adshare.services.market_data import MarketDataService

logger = get_logger(__name__)
router = APIRouter(prefix="/historical", tags=["historical"])


def _require_enabled() -> None:
    settings = get_settings()
    if not settings.historical_enabled:
        raise HTTPException(
            status_code=503,
            detail="historical warehouse is disabled (set HISTORICAL_ENABLED=true)",
        )


@router.get("/kline", response_model=HistoricalKlineResponse)
async def historical_kline(
    codes: str = Query(..., description="Comma-separated stock codes"),
    begin_date: int = Query(..., description="Start date YYYYMMDD"),
    end_date: int = Query(..., description="End date YYYYMMDD"),
    period: str = Query(default="day", description="Period: day, week, month"),
    limit: Optional[int] = Query(default=None, description="Max records"),
    offset: int = Query(default=0, description="Records to skip"),
    source: str = Query(
        default="auto",
        description="Data source: auto (warehouse when synced, else SDK), warehouse, sdk",
    ),
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Return K-line data from the on-disk Parquet warehouse."""
    _require_enabled()
    code_list = [c.strip() for c in codes.split(",") if c.strip()]

    # Defensive: empty codes yields an empty result; don't fall through to the SDK.
    if not code_list:
        return HistoricalKlineResponse(
            codes=[],
            period=period,
            begin_date=begin_date,
            end_date=end_date,
            source="none",
            synced=False,
            count=0,
            data=[],
        )

    try:
        result = service.get_kline(
            codes=code_list,
            begin_date=begin_date,
            end_date=end_date,
            period=period,
            limit=limit,
            offset=offset,
            source=source,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("kline query failed: %s", e)
        raise HTTPException(status_code=500, detail=f"kline query failed: {e}")

    records = dataframe_to_historical_kline_records(result.df)
    return HistoricalKlineResponse(
        codes=code_list,
        period=period,
        begin_date=begin_date,
        end_date=end_date,
        source=result.source,
        synced=result.synced,
        count=len(records),
        data=records,
    )


@router.get("/calendar", response_model=HistoricalCalendarResponse)
async def historical_calendar(
    market: Optional[str] = Query(default=None, description="Market filter"),
    begin_date: Optional[int] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[int] = Query(default=None, description="End date YYYYMMDD"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Return the trading calendar from the warehouse."""
    _require_enabled()
    if warehouse is None:
        raise HTTPException(status_code=503, detail="historical warehouse is not available")

    df = warehouse.query_calendar(
        market=market,
        begin_date=begin_date,
        end_date=end_date,
    )
    data = df.to_dict("records") if not df.empty else []
    return HistoricalCalendarResponse(
        market=market or "ALL",
        count=len(data),
        data=data,
    )


@router.get("/codes", response_model=HistoricalCodesResponse)
async def historical_codes(
    board: Optional[str] = Query(default=None, description="Filter by board"),
    is_listed: Optional[bool] = Query(default=None, description="Filter by listing status"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Return the codes table from the warehouse."""
    _require_enabled()
    if warehouse is None:
        raise HTTPException(status_code=503, detail="historical warehouse is not available")

    df = warehouse.query_codes(board=board, is_listed=is_listed)
    data = df.to_dict("records") if not df.empty else []
    return HistoricalCodesResponse(count=len(data), data=data)


@router.post("/sql", response_model=HistoricalSqlResponse)
async def historical_sql(
    request: HistoricalSqlRequest,
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Run a constrained SELECT against the warehouse."""
    _require_enabled()
    if warehouse is None:
        raise HTTPException(status_code=503, detail="historical warehouse is not available")

    settings = get_settings()
    cap = int(request.max_rows) if request.max_rows else int(settings.duckdb_max_rows)
    cap = max(1, min(cap, 1_000_000))
    try:
        df = warehouse.execute_sql(request.sql, max_rows=cap)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"sql execution failed: {e}")

    truncated = len(df) > cap
    if truncated:
        df = df.head(cap)

    rows = dataframe_to_json_rows(df)

    return HistoricalSqlResponse(
        columns=list(df.columns),
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        count=len(rows),
    )
