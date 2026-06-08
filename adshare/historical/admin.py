"""Admin endpoints for the historical data warehouse.

Routes:

* ``GET  /historical/admin/health``  — warehouse health probe
* ``GET  /historical/admin/stats``   — file/year/byte counts
* ``POST /historical/admin/sync``    — trigger a sync job synchronously
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from adshare.core.config import get_settings
from adshare.historical.sync import (
    sync_kline_daily,
    sync_kline_monthly,
    sync_kline_weekly,
    sync_meta_calendar,
    sync_meta_codes,
)
from adshare.historical.warehouse import get_warehouse

router = APIRouter(prefix="/historical/admin", tags=["historical-admin"])


@router.get("/health")
async def admin_health() -> dict:
    """Return warehouse health info."""
    settings = get_settings()
    warehouse = get_warehouse(settings)
    return {
        "settings": {
            "historical_enabled": settings.historical_enabled,
            "duckdb_mode": settings.duckdb_mode,
            "sync_schedule_enabled": settings.sync_schedule_enabled,
        },
        "warehouse": warehouse.health(),
    }


@router.get("/stats")
async def admin_stats() -> dict:
    """Return aggregate warehouse statistics."""
    settings = get_settings()
    warehouse = get_warehouse(settings)
    return warehouse.stats()


@router.post("/sync")
async def admin_sync(
    job: str = Query(..., description="sync job: daily, weekly, monthly, codes, calendar"),
    year: Optional[int] = Query(default=None, description="Year for K-line jobs"),
    market: str = Query(default="SH", description="Market for calendar job"),
) -> dict:
    """Trigger a sync job synchronously."""
    settings = get_settings()
    if not settings.historical_enabled:
        raise HTTPException(status_code=400, detail="historical_enabled is false")

    job_lc = (job or "").lower()
    started = time.time()
    if job_lc in {"daily", "kline_daily"}:
        result = sync_kline_daily(year=year)
    elif job_lc in {"weekly", "kline_weekly"}:
        result = sync_kline_weekly(year=year)
    elif job_lc in {"monthly", "kline_monthly"}:
        result = sync_kline_monthly(year=year)
    elif job_lc in {"codes", "meta_codes"}:
        result = sync_meta_codes()
    elif job_lc in {"calendar", "meta_calendar"}:
        result = sync_meta_calendar(market=market)
    else:
        raise HTTPException(status_code=400, detail=f"unknown job: {job}")
    payload = result.to_dict()
    payload["wall_duration"] = time.time() - started
    return payload
