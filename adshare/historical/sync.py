"""Sync jobs for the L3 historical data warehouse.

The module implements:

* ``sync_kline_daily`` / ``sync_kline_weekly`` / ``sync_kline_monthly`` — pull
  the year's K-line data from the SDK and write a per-stock Parquet file.
* ``sync_meta_codes`` / ``sync_meta_calendar`` — refresh the global meta
  Parquet files used by DuckDB views.
* :class:`SyncResult` — small data class describing the outcome of a run.
* :func:`init_scheduler` / :func:`start_scheduler` / :func:`shutdown_scheduler`
  — APScheduler glue for the FastAPI lifespan.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import pandas as pd

from adshare.core.config import Settings, get_settings
from adshare.core.logging import get_logger
from adshare.historical.models import (
    KLINE_COLUMNS,
    kline_file_path,
    normalize_period,
    standardize_calendar_df,
    standardize_codes_df,
    standardize_kline_df,
    validate_kline_df,
    write_metadata,
)
from adshare.historical.warehouse import HistoricalWarehouse, get_warehouse

logger = get_logger(__name__)


# ----------------------------------------------------------------------
# Result helpers
# ----------------------------------------------------------------------

@dataclass
class SyncResult:
    """Aggregate outcome of a single sync job run."""

    job: str
    started_at: float
    finished_at: float = 0.0
    success: bool = False
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    rows: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.finished_at - self.started_at

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------

def _get_adapter_safe():
    """Lazily import the SDK adapter so tests that mock it still work."""
    from adshare.adapters.amazingdata import get_adapter

    return get_adapter()


def _existing_codes(period: str, year: int, root: Path) -> set[str]:
    """Return the set of codes already present for a (period, year)."""
    year_dir = root / "A_share" / normalize_period(period) / str(int(year))
    if not year_dir.exists():
        return set()
    return {f.stem for f in year_dir.glob("*.parquet")}


def _persist_kline(
    df: pd.DataFrame,
    period: str,
    year: int,
    code: str,
    root: Path,
) -> Optional[Path]:
    """Standardize, validate and write one stock's Parquet file."""
    if df is None or df.empty:
        return None
    std = standardize_kline_df(df, code=code)
    std = validate_kline_df(std)
    if std.empty:
        return None
    file_path = kline_file_path(root, period, year, code)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    std.to_parquet(file_path, engine="pyarrow", compression="zstd", index=False)
    return file_path


def _persist_meta(df: pd.DataFrame, path: Path) -> Optional[Path]:
    if df is None or df.empty:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
    return path


def _year_bounds(year: int, today: Optional[datetime] = None) -> tuple[int, int]:
    today = today or datetime.now()
    end = int(today.strftime("%Y%m%d"))
    begin = int(f"{int(year)}0101")
    return begin, min(end, int(f"{int(year)}1231"))


# ----------------------------------------------------------------------
# Sync jobs
# ----------------------------------------------------------------------

def sync_kline(
    period: str = "day",
    *,
    year: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Generic K-line sync that handles daily/weekly/monthly periods.

    Pulls each stock's full-year data from the SDK and writes a per-stock
    Parquet file. Failures are captured in the result's ``errors`` list.
    """
    settings = settings or get_settings()
    warehouse = warehouse or get_warehouse(settings)
    root = warehouse.root
    today = datetime.now()
    target_year = int(year or today.year)

    if codes is None:
        try:
            adapter_obj = adapter or _get_adapter_safe()
            codes = list(adapter_obj.get_code_list("EXTRA_STOCK_A_SH_SZ"))
        except Exception as e:
            logger.error("sync_kline(%s): failed to fetch code list: %s", period, e)
            return SyncResult(
                job=f"sync_kline_{normalize_period(period)}",
                started_at=time.time(),
                finished_at=time.time(),
                success=False,
                errors=[f"code list fetch failed: {e}"],
            )
    codes = list(codes)
    if not codes:
        return SyncResult(
            job=f"sync_kline_{normalize_period(period)}",
            started_at=time.time(),
            finished_at=time.time(),
            success=True,
        )

    job_name = f"sync_kline_{normalize_period(period)}"
    result = SyncResult(job=job_name, started_at=time.time(), total=len(codes))
    begin_date, end_date = _year_bounds(target_year, today)

    adapter_obj = adapter or _get_adapter_safe()

    def _sync_one(code: str) -> tuple[str, bool, Optional[Path], Optional[str]]:
        attempts = max(1, int(settings.sync_retry_attempts))
        for attempt in range(attempts):
            try:
                df = adapter_obj.get_kline(
                    codes=code,
                    begin_date=begin_date,
                    end_date=end_date,
                    period=period,
                )
                path = _persist_kline(df, period, target_year, code, root)
                return code, True, path, None
            except Exception as e:  # noqa: BLE001
                err_str = str(e).lower()
                if "exceed the max limitation" in err_str or "rate limit" in err_str:
                    time.sleep(0.5 * (attempt + 1))
                if attempt == attempts - 1:
                    return code, False, None, str(e)
                time.sleep(0.2 * (attempt + 1))
        return code, False, None, "unknown"

    rows_written = 0
    file_count = 0
    workers = max(1, int(settings.sync_workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_sync_one, c): c for c in codes}
        for fut in as_completed(futures):
            code, ok, path, err = fut.result()
            if ok:
                result.succeeded += 1
                if path is not None:
                    file_count += 1
                    try:
                        # Reload to count rows cheaply
                        rows_written += len(pd.read_parquet(path))
                    except Exception:
                        pass
            else:
                result.failed += 1
                if err:
                    result.errors.append(f"{code}: {err}")

    result.rows = rows_written
    result.finished_at = time.time()
    result.success = result.failed == 0

    try:
        existing = _existing_codes(period, target_year, root)
        write_metadata(
            root,
            period,
            target_year,
            file_count=len(existing),
            total_rows=rows_written,
            last_sync_at=int(time.time()),
        )
    except Exception as e:
        logger.warning("sync_kline: failed to write metadata: %s", e)

    warehouse.refresh_views()
    logger.info(
        "sync_kline(%s) year=%s succeeded=%d failed=%d rows=%d duration=%.2fs",
        period, target_year, result.succeeded, result.failed, result.rows, result.duration,
    )
    return result


def sync_kline_daily(
    *,
    year: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    return sync_kline(
        "day",
        year=year,
        codes=codes,
        settings=settings,
        warehouse=warehouse,
        adapter=adapter,
    )


def sync_kline_weekly(
    *,
    year: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    return sync_kline(
        "week",
        year=year,
        codes=codes,
        settings=settings,
        warehouse=warehouse,
        adapter=adapter,
    )


def sync_kline_monthly(
    *,
    year: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    return sync_kline(
        "month",
        year=year,
        codes=codes,
        settings=settings,
        warehouse=warehouse,
        adapter=adapter,
    )


# ----------------------------------------------------------------------
# Meta sync
# ----------------------------------------------------------------------

def sync_meta_codes(
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Refresh ``meta/codes.parquet`` from the SDK."""
    settings = settings or get_settings()
    warehouse = warehouse or get_warehouse(settings)
    result = SyncResult(job="sync_meta_codes", started_at=time.time())
    try:
        adapter_obj = adapter or _get_adapter_safe()
        raw = adapter_obj.get_code_info(security_type="EXTRA_STOCK_A")
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            raw = adapter_obj.get_stock_basic(summary_only=False)
        std = standardize_codes_df(raw)
        path = _persist_meta(std, warehouse.meta_dir() / "codes.parquet")
        result.success = path is not None
        result.rows = len(std) if std is not None else 0
    except Exception as e:
        logger.error("sync_meta_codes failed: %s", e)
        result.errors.append(str(e))
    result.finished_at = time.time()
    warehouse.refresh_views()
    return result


def sync_meta_calendar(
    market: str = "SH",
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Refresh ``meta/calendar.parquet`` from the SDK."""
    settings = settings or get_settings()
    warehouse = warehouse or get_warehouse(settings)
    result = SyncResult(job=f"sync_meta_calendar[{market}]", started_at=time.time())
    try:
        adapter_obj = adapter or _get_adapter_safe()
        raw = adapter_obj.get_calendar(market=market)
        std = standardize_calendar_df(raw, market=market)
        path = _persist_meta(std, warehouse.meta_dir() / "calendar.parquet")
        result.success = path is not None
        result.rows = len(std) if std is not None else 0
    except Exception as e:
        logger.error("sync_meta_calendar failed: %s", e)
        result.errors.append(str(e))
    result.finished_at = time.time()
    warehouse.refresh_views()
    return result


# ----------------------------------------------------------------------
# APScheduler glue
# ----------------------------------------------------------------------

_scheduler: Optional["BackgroundScheduler"] = None  # type: ignore[name-defined]
_scheduler_lock = threading.Lock()


def init_scheduler(settings: Optional[Settings] = None) -> "BackgroundScheduler":  # type: ignore[name-defined]
    """Initialise the APScheduler instance if needed."""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            return _scheduler
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("APScheduler is not installed") from e

        settings = settings or get_settings()
        scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        if settings.sync_schedule_enabled:
            scheduler.add_job(
                _run_sync_kline_daily,
                "cron",
                hour=int(settings.sync_kline_daily_hour),
                minute=int(settings.sync_kline_daily_minute),
                id="sync_kline_daily",
                replace_existing=True,
            )
            scheduler.add_job(
                _run_sync_kline_weekly,
                "cron",
                day_of_week="fri",
                hour=int(settings.sync_kline_weekly_hour),
                minute=int(settings.sync_kline_weekly_minute),
                id="sync_kline_weekly",
                replace_existing=True,
            )
            scheduler.add_job(
                _run_sync_kline_monthly,
                "cron",
                day=1,
                hour=int(settings.sync_kline_monthly_hour),
                minute=int(settings.sync_kline_monthly_minute),
                id="sync_kline_monthly",
                replace_existing=True,
            )
            scheduler.add_job(
                _run_sync_meta_codes,
                "cron",
                hour=int(settings.sync_meta_codes_hour),
                minute=int(settings.sync_meta_codes_minute),
                id="sync_meta_codes",
                replace_existing=True,
            )
        _scheduler = scheduler
        return scheduler


def start_scheduler() -> Optional["BackgroundScheduler"]:  # type: ignore[name-defined]
    """Initialise and start the scheduler if it isn't already running."""
    scheduler = init_scheduler()
    with _scheduler_lock:
        if not scheduler.running:
            scheduler.start()
    return scheduler


def shutdown_scheduler() -> None:
    """Shut the scheduler down (idempotent)."""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None and _scheduler.running:
            try:
                _scheduler.shutdown(wait=False)
            except Exception:
                pass
        _scheduler = None


def get_scheduler() -> Optional["BackgroundScheduler"]:  # type: ignore[name-defined]
    return _scheduler


def _run_sync_kline_daily() -> None:
    try:
        sync_kline_daily()
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_kline_daily failed: %s", e)


def _run_sync_kline_weekly() -> None:
    try:
        sync_kline_weekly()
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_kline_weekly failed: %s", e)


def _run_sync_kline_monthly() -> None:
    try:
        sync_kline_monthly()
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_kline_monthly failed: %s", e)


def _run_sync_meta_codes() -> None:
    try:
        sync_meta_codes()
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_meta_codes failed: %s", e)
