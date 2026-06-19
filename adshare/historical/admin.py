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
from adshare.historical.maintenance import (
    repair_all,
    repair_codes_table,
    repair_financial_table,
    repair_kline_directory,
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
    year: Optional[int] = Query(default=None, description="[deprecated] Year anchor; use from_date/to_date instead"),
    from_date: Optional[int] = Query(default=None, description="Inclusive start date YYYYMMDD"),
    to_date: Optional[int] = Query(default=None, description="Inclusive end date YYYYMMDD"),
    market: str = Query(default="SH", description="Market for calendar job"),
) -> dict:
    """Trigger a sync job synchronously."""
    settings = get_settings()
    if not settings.historical_enabled:
        raise HTTPException(status_code=400, detail="historical_enabled is false")

    job_lc = (job or "").lower()
    started = time.time()
    if job_lc in {"daily", "kline_daily"}:
        result = sync_kline_daily(year=year, from_date=from_date, to_date=to_date)
    elif job_lc in {"weekly", "kline_weekly"}:
        result = sync_kline_weekly(year=year, from_date=from_date, to_date=to_date)
    elif job_lc in {"monthly", "kline_monthly"}:
        result = sync_kline_monthly(year=year, from_date=from_date, to_date=to_date)
    elif job_lc in {"codes", "meta_codes"}:
        result = sync_meta_codes()
    elif job_lc in {"calendar", "meta_calendar"}:
        result = sync_meta_calendar(market=market)
    else:
        raise HTTPException(status_code=400, detail=f"unknown job: {job}")
    payload = result.to_dict()
    payload["wall_duration"] = time.time() - started
    return payload


@router.post("/repair")
async def admin_repair(
    job: str = Query(
        "all",
        description="repair job: kline, codes, financial, or all",
    ),
    dry_run: bool = Query(
        default=False,
        description="If true, read+fix in memory but do not write back.",
    ),
) -> dict:
    """Run an idempotent warehouse repair routine.

    Each routine is safe to call repeatedly. Returns the per-job
    :class:`MaintenanceResult` so the operator can audit what
    actually changed.
    """
    job_lc = (job or "all").lower()
    settings = get_settings()
    warehouse = get_warehouse(settings)
    started = time.time()
    if job_lc == "kline":
        results = [repair_kline_directory(dry_run=dry_run, warehouse=warehouse)]
    elif job_lc == "codes":
        results = [repair_codes_table(dry_run=dry_run, warehouse=warehouse)]
    elif job_lc == "financial":
        results = [repair_financial_table(dry_run=dry_run, warehouse=warehouse)]
    elif job_lc == "all":
        results = repair_all(dry_run=dry_run, warehouse=warehouse)
    else:
        raise HTTPException(status_code=400, detail=f"unknown job: {job}")
    return {
        "job": job_lc,
        "dry_run": dry_run,
        "wall_duration": time.time() - started,
        "results": [r.to_dict() for r in results],
    }
