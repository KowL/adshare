"""Admin endpoints for the historical data warehouse.

Routes:

* ``GET  /historical/admin/health``  — warehouse health probe
* ``GET  /historical/admin/stats``   — file/year/byte counts
* ``POST /historical/admin/repair``  — run an idempotent repair routine

Sync jobs are **not** exposed here: they require a data-source session and
run only in the worker process (see :mod:`amazingdata.batch`), on a
schedule or via ``SYNC_ON_START``.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query

from adshare.core.config import get_settings
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
