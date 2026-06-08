"""Public HTTP endpoints for the L3 historical data warehouse.

Exposes read access to the on-disk Parquet files via DuckDB. When
``HISTORICAL_ENABLED=false`` the endpoints return 503 with a clear
message; SDK fallback is intentionally opt-in to keep behaviour
predictable.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from adshare.adapters.amazingdata import get_adapter
from adshare.core.config import get_settings
from adshare.core.logging import get_logger
from adshare.historical.warehouse import get_warehouse
from adshare.models.schemas import (
    HistoricalCalendarResponse,
    HistoricalCodesResponse,
    HistoricalKlineRecord,
    HistoricalKlineResponse,
    HistoricalSqlRequest,
    HistoricalSqlResponse,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/historical", tags=["historical"])


def _require_enabled() -> None:
    settings = get_settings()
    if not settings.historical_enabled:
        raise HTTPException(
            status_code=503,
            detail="historical warehouse is disabled (set HISTORICAL_ENABLED=true)",
        )


def _df_to_kline_records(df: pd.DataFrame) -> List[HistoricalKlineRecord]:
    records: List[HistoricalKlineRecord] = []
    if df is None or df.empty:
        return records
    for _, row in df.iterrows():
        records.append(
            HistoricalKlineRecord(
                code=str(row.get("code", "")),
                date=int(row.get("date", 0) or 0),
                open=float(row.get("open", 0) or 0.0),
                high=float(row.get("high", 0) or 0.0),
                low=float(row.get("low", 0) or 0.0),
                close=float(row.get("close", 0) or 0.0),
                volume=int(row.get("volume", 0) or 0),
                amount=float(row.get("amount", 0) or 0.0),
                adj_factor=(
                    float(row.get("adj_factor"))
                    if "adj_factor" in row and pd.notna(row.get("adj_factor"))
                    else None
                ),
                is_suspended=(
                    bool(row.get("is_suspended"))
                    if "is_suspended" in row and pd.notna(row.get("is_suspended"))
                    else None
                ),
                sync_at=(
                    int(row.get("sync_at"))
                    if "sync_at" in row and pd.notna(row.get("sync_at"))
                    else None
                ),
            )
        )
    return records


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
):
    """Return K-line data from the on-disk Parquet warehouse."""
    _require_enabled()
    settings = get_settings()
    warehouse = get_warehouse(settings)
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

    period_map = {"day": "day", "week": "week", "month": "month"}
    sdk_period = period_map.get(period.lower(), "day")

    df = pd.DataFrame()
    used_source = "warehouse"
    synced = False
    if source in {"auto", "warehouse"} and warehouse.is_synced(begin_date, end_date, period, code_list):
        try:
            df = warehouse.query_kline(
                code_list, begin_date, end_date, period,
                limit=limit, offset=offset,
            )
            synced = True
        except Exception as e:
            logger.warning("warehouse query failed, falling back to SDK: %s", e)
            df = pd.DataFrame()

    if df.empty and source in {"auto", "sdk"}:
        try:
            adapter = get_adapter()
            df = adapter.get_kline(
                codes=",".join(code_list),
                begin_date=begin_date,
                end_date=end_date,
                period=sdk_period,
                limit=limit,
                offset=offset,
            )
            used_source = "sdk"
        except Exception as e:
            logger.error("SDK kline fallback failed: %s", e)
            raise HTTPException(status_code=500, detail=f"SDK kline fallback failed: {e}")

    records = _df_to_kline_records(df)
    return HistoricalKlineResponse(
        codes=code_list,
        period=period,
        begin_date=begin_date,
        end_date=end_date,
        source=used_source,
        synced=synced,
        count=len(records),
        data=records,
    )


@router.get("/calendar", response_model=HistoricalCalendarResponse)
async def historical_calendar(
    market: Optional[str] = Query(default=None, description="Market filter"),
    begin_date: Optional[int] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[int] = Query(default=None, description="End date YYYYMMDD"),
):
    """Return the trading calendar from the warehouse."""
    _require_enabled()
    settings = get_settings()
    warehouse = get_warehouse(settings)
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
):
    """Return the codes table from the warehouse."""
    _require_enabled()
    settings = get_settings()
    warehouse = get_warehouse(settings)
    df = warehouse.query_codes(board=board, is_listed=is_listed)
    data = df.to_dict("records") if not df.empty else []
    return HistoricalCodesResponse(count=len(data), data=data)


@router.post("/sql", response_model=HistoricalSqlResponse)
async def historical_sql(request: HistoricalSqlRequest):
    """Run a constrained SELECT against the warehouse."""
    _require_enabled()
    settings = get_settings()
    warehouse = get_warehouse(settings)
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

    rows: List[List] = []
    for _, row in df.iterrows():
        rows.append([_jsonify(v) for v in row.tolist()])

    return HistoricalSqlResponse(
        columns=list(df.columns),
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        count=len(rows),
    )


def _jsonify(value):
    """Make pandas/numpy values JSON-friendly."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (pd.Timestamp, datetime)):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return str(value)


# numpy import (lazy) — kept here to avoid module-level dependency for the
# routers that don't need SQL.
import numpy as np  # noqa: E402
