"""Sync jobs for the L3 historical data warehouse.

The module implements:

* ``sync_kline_daily`` / ``sync_kline_weekly`` / ``sync_kline_monthly`` — pull
  K-line data from the SDK and overwrite the per-stock Parquet file
  (one file per code with all years merged).
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

# Default earliest date the sync job will pull from. Mirrors the
# ``default_begin_date`` constant in :mod:`adshare.core.config`.
_DEFAULT_BEGIN_DATE = 20200101


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
    """Lazily import the SDK adapter from the worker package."""
    from amazingdata_worker.adapters.amazingdata import get_adapter

    return get_adapter()


def _ensure_code_suffix(code: str) -> str:
    """Append .SH/.SZ/.BJ suffix if missing (matches TGW SDK convention)."""
    c = code.strip()
    if "." in c:
        return c
    if len(c) == 6 and c.isdigit():
        if c.startswith(("60", "68", "69")):
            return f"{c}.SH"
        elif c.startswith(("00", "30", "39")):
            return f"{c}.SZ"
        elif c.startswith(("8", "4", "9")):
            return f"{c}.BJ"
    return c


def _existing_codes(period: str, root: Path) -> set[str]:
    """Return the set of codes already present for a period (flat layout)."""
    period_dir = root / "A_share" / normalize_period(period)
    if not period_dir.exists():
        return set()
    return {f.stem for f in period_dir.glob("*.parquet")}


def _persist_kline(
    df: pd.DataFrame,
    period: str,
    code: str,
    root: Path,
) -> Optional[Path]:
    """Standardize, validate and overwrite one stock's Parquet file."""
    if df is None or df.empty:
        return None
    std = standardize_kline_df(df, code=code)
    std = validate_kline_df(std)
    if std.empty:
        return None
    # Ensure code has .SH/.SZ/.BJ suffix for consistent file naming
    code_key = _ensure_code_suffix(code)
    file_path = kline_file_path(root, period, code_key)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    std.to_parquet(file_path, engine="pyarrow", compression="zstd", index=False)
    return file_path


def _persist_meta(df: pd.DataFrame, path: Path) -> Optional[Path]:
    if df is None or df.empty:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
    return path


def _date_bounds(
    from_date: Optional[int],
    to_date: Optional[int],
    today: Optional[datetime] = None,
) -> tuple[int, int]:
    """Resolve inclusive (begin, end) date integers for a sync run.

    Defaults: ``from_date = 20200101``, ``to_date = today``.
    """
    today = today or datetime.now()
    end_default = int(today.strftime("%Y%m%d"))
    begin = int(from_date) if from_date is not None else _DEFAULT_BEGIN_DATE
    end = int(to_date) if to_date is not None else end_default
    if begin > end:
        begin, end = end, begin
    return begin, end


# ----------------------------------------------------------------------
# Sync jobs
# ----------------------------------------------------------------------

def sync_kline(
    period: str = "day",
    *,
    from_date: Optional[int] = None,
    to_date: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    batch_size: Optional[int] = None,
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Generic K-line sync that handles daily/weekly/monthly periods.

    Pulls each stock's full data range from the SDK and **overwrites** the
    per-stock Parquet file (one file per code, all years merged). The
    default pull range is ``[20200101, today]``. Pass ``from_date`` /
    ``to_date`` to narrow or extend the window.

    The ``year`` keyword is accepted for backward compatibility and is
    translated to a ``from_date`` of ``{year}0101`` and a ``to_date`` of
    min(today, ``{year}1231``).
    """
    settings = settings or get_settings()
    warehouse = warehouse or get_warehouse(settings)
    root = warehouse.root
    today = datetime.now()
    begin_date, end_date = _date_bounds(from_date, to_date, today)

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

    adapter_obj = adapter or _get_adapter_safe()
    batch_size = int(batch_size or 1)
    if batch_size > 1:
        rows_written = 0
        attempts = max(1, int(settings.sync_retry_attempts))
        for start in range(0, len(codes), batch_size):
            batch = codes[start : start + batch_size]
            batch_label = f"{start + 1}-{start + len(batch)}"
            batch_df = pd.DataFrame()
            batch_error: Optional[str] = None
            for attempt in range(attempts):
                try:
                    batch_df = adapter_obj.get_kline(
                        codes=",".join(batch),
                        begin_date=begin_date,
                        end_date=end_date,
                        period=period,
                    )
                    batch_error = None
                    break
                except Exception as e:  # noqa: BLE001
                    batch_error = str(e)
                    err_str = batch_error.lower()
                    if "exceed the max limitation" in err_str or "rate limit" in err_str:
                        time.sleep(0.5 * (attempt + 1))
                    if attempt < attempts - 1:
                        time.sleep(0.2 * (attempt + 1))

            if batch_error is not None:
                result.failed += len(batch)
                result.errors.append(f"batch {batch_label}: {batch_error}")
                continue

            if batch_df is None or batch_df.empty:
                result.skipped += len(batch)
                continue

            for code in batch:
                code_key = _ensure_code_suffix(code)
                if "code" in batch_df.columns:
                    code_df = batch_df[batch_df["code"].astype(str) == code_key]
                else:
                    code_df = batch_df if len(batch) == 1 else pd.DataFrame()
                path = _persist_kline(code_df, period, code, root)
                if path is None:
                    result.skipped += 1
                    continue
                result.succeeded += 1
                try:
                    rows_written += len(pd.read_parquet(path))
                except Exception:
                    pass

            logger.info(
                "sync_kline(%s) range=[%s,%s] batch=%s/%s succeeded=%d skipped=%d failed=%d rows=%d",
                period,
                begin_date,
                end_date,
                min(start + len(batch), len(codes)),
                len(codes),
                result.succeeded,
                result.skipped,
                result.failed,
                rows_written,
            )

        result.rows = rows_written
        result.finished_at = time.time()
        result.success = result.failed == 0
        _write_period_metadata(period, root, warehouse, rows_written)
        logger.info(
            "sync_kline(%s) range=[%s,%s] succeeded=%d skipped=%d failed=%d rows=%d duration=%.2fs",
            period,
            begin_date,
            end_date,
            result.succeeded,
            result.skipped,
            result.failed,
            result.rows,
            result.duration,
        )
        return result

    def _sync_one(code: str) -> tuple[str, str, Optional[Path], Optional[str]]:
        attempts = max(1, int(settings.sync_retry_attempts))
        for attempt in range(attempts):
            try:
                df = adapter_obj.get_kline(
                    codes=code,
                    begin_date=begin_date,
                    end_date=end_date,
                    period=period,
                )
                path = _persist_kline(df, period, code, root)
                if path is None:
                    return code, "skipped", None, None
                return code, "written", path, None
            except Exception as e:  # noqa: BLE001
                err_str = str(e).lower()
                if "exceed the max limitation" in err_str or "rate limit" in err_str:
                    time.sleep(0.5 * (attempt + 1))
                if attempt == attempts - 1:
                    return code, "failed", None, str(e)
                time.sleep(0.2 * (attempt + 1))
        return code, "failed", None, "unknown"

    rows_written = 0
    file_count = 0
    workers = max(1, int(settings.sync_workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_sync_one, c): c for c in codes}
        for fut in as_completed(futures):
            code, status, path, err = fut.result()
            if status == "written":
                result.succeeded += 1
                file_count += 1
                try:
                    rows_written += len(pd.read_parquet(path))
                except Exception:
                    pass
            elif status == "skipped":
                result.skipped += 1
            else:
                result.failed += 1
                if err:
                    result.errors.append(f"{code}: {err}")

    result.rows = rows_written
    result.finished_at = time.time()
    result.success = result.failed == 0
    _write_period_metadata(period, root, warehouse, rows_written)
    logger.info(
        "sync_kline(%s) range=[%s,%s] succeeded=%d failed=%d rows=%d duration=%.2fs",
        period,
        begin_date,
        end_date,
        result.succeeded,
        result.failed,
        result.rows,
        result.duration,
    )
    return result


def _write_period_metadata(
    period: str,
    root: Path,
    warehouse: HistoricalWarehouse,
    rows_written: int,
) -> None:
    """Refresh the per-period ``_metadata.json`` and DuckDB views."""
    try:
        existing = _existing_codes(period, root)
        # Refresh DuckDB views first so the date-range probe sees the new files.
        warehouse.refresh_views()
        # Pull min/max date via DuckDB (single scan over the view).
        first_date: Optional[int] = None
        last_date: Optional[int] = None
        subdir = normalize_period(period)
        view_map = {"daily": "v_kline_day", "weekly": "v_kline_week", "monthly": "v_kline_month"}
        view = view_map.get(subdir)
        if view and existing:
            try:
                row = warehouse.connection.execute(
                    f"SELECT MIN(date), MAX(date) FROM {view}"
                ).fetchone()
                if row:
                    first_date, last_date = row[0], row[1]
            except Exception as e:  # noqa: BLE001
                logger.debug("metadata date range probe failed: %s", e)
        write_metadata(
            root,
            period,
            file_count=len(existing),
            total_rows=rows_written,
            first_date=first_date,
            last_date=last_date,
            last_sync_at=int(time.time()),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("sync_kline: failed to write metadata: %s", e)


def sync_kline_daily(
    *,
    year: Optional[int] = None,
    from_date: Optional[int] = None,
    to_date: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    batch_size: Optional[int] = None,
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    if year is not None and from_date is None and to_date is None:
        today = datetime.now()
        end_cap = int(today.strftime("%Y%m%d"))
        from_date = int(f"{int(year)}0101")
        to_date = min(end_cap, int(f"{int(year)}1231"))
    return sync_kline(
        "day",
        from_date=from_date,
        to_date=to_date,
        codes=codes,
        batch_size=batch_size,
        settings=settings,
        warehouse=warehouse,
        adapter=adapter,
    )


def sync_kline_weekly(
    *,
    year: Optional[int] = None,
    from_date: Optional[int] = None,
    to_date: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    batch_size: Optional[int] = None,
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    if year is not None and from_date is None and to_date is None:
        today = datetime.now()
        end_cap = int(today.strftime("%Y%m%d"))
        from_date = int(f"{int(year)}0101")
        to_date = min(end_cap, int(f"{int(year)}1231"))
    return sync_kline(
        "week",
        from_date=from_date,
        to_date=to_date,
        codes=codes,
        batch_size=batch_size,
        settings=settings,
        warehouse=warehouse,
        adapter=adapter,
    )


def sync_kline_monthly(
    *,
    year: Optional[int] = None,
    from_date: Optional[int] = None,
    to_date: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    batch_size: Optional[int] = None,
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    if year is not None and from_date is None and to_date is None:
        today = datetime.now()
        end_cap = int(today.strftime("%Y%m%d"))
        from_date = int(f"{int(year)}0101")
        to_date = min(end_cap, int(f"{int(year)}1231"))
    return sync_kline(
        "month",
        from_date=from_date,
        to_date=to_date,
        codes=codes,
        batch_size=batch_size,
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
            # Reference data sync (weekly)
            scheduler.add_job(
                _run_sync_financial,
                "cron",
                day_of_week="sat",
                hour=2,
                minute=0,
                id="sync_financial",
                replace_existing=True,
            )
            scheduler.add_job(
                _run_sync_shareholder,
                "cron",
                day_of_week="sat",
                hour=3,
                minute=0,
                id="sync_shareholder",
                replace_existing=True,
            )
            scheduler.add_job(
                _run_sync_index_component,
                "cron",
                day_of_week="sat",
                hour=4,
                minute=0,
                id="sync_index_component",
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
        settings = get_settings()
        warehouse = get_warehouse(settings)
        end_date = int(datetime.now().strftime("%Y%m%d"))
        begin_date = 20200101
        try:
            warehouse.refresh_views()
            row = warehouse.connection.execute("SELECT MAX(date) FROM v_kline_day").fetchone()
            last_date = row[0] if row and row[0] else None
            if last_date:
                begin_date = int(last_date)
        except Exception:
            logger.warning("scheduled sync_kline_daily: failed to probe last date, using 20200101")
        sync_kline_daily(from_date=begin_date, to_date=end_date)
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_kline_daily failed: %s", e)


def _run_sync_kline_weekly() -> None:
    try:
        settings = get_settings()
        warehouse = get_warehouse(settings)
        end_date = int(datetime.now().strftime("%Y%m%d"))
        begin_date = 20200101
        try:
            warehouse.refresh_views()
            row = warehouse.connection.execute("SELECT MAX(date) FROM v_kline_week").fetchone()
            last_date = row[0] if row and row[0] else None
            if last_date:
                begin_date = int(last_date)
        except Exception:
            logger.warning("scheduled sync_kline_weekly: failed to probe last date, using 20200101")
        sync_kline_weekly(from_date=begin_date, to_date=end_date)
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_kline_weekly failed: %s", e)


def _run_sync_kline_monthly() -> None:
    try:
        settings = get_settings()
        warehouse = get_warehouse(settings)
        end_date = int(datetime.now().strftime("%Y%m%d"))
        begin_date = 20200101
        try:
            warehouse.refresh_views()
            row = warehouse.connection.execute("SELECT MAX(date) FROM v_kline_month").fetchone()
            last_date = row[0] if row and row[0] else None
            if last_date:
                begin_date = int(last_date)
        except Exception:
            logger.warning("scheduled sync_kline_monthly: failed to probe last date, using 20200101")
        sync_kline_monthly(from_date=begin_date, to_date=end_date)
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_kline_monthly failed: %s", e)


def _run_sync_meta_codes() -> None:
    try:
        sync_meta_codes()
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_meta_codes failed: %s", e)



# ----------------------------------------------------------------------
# Reference data sync (financial, shareholder, index component)
# ----------------------------------------------------------------------

def _persist_reference(df: pd.DataFrame, path: Path) -> Optional[Path]:
    """Persist a reference DataFrame to a Parquet file."""
    if df is None or df.empty:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
    return path


def sync_financial(
    statement_type: str = "balance",
    *,
    codes: Optional[Sequence[str]] = None,
    batch_size: int = 50,
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Sync financial statement data to the warehouse reference table.

    Args:
        statement_type: "balance", "income", or "cashflow".
        codes: Optional list of codes. Defaults to all A-share stocks.
        batch_size: Number of codes per SDK call.
    """
    settings = settings or get_settings()
    warehouse = warehouse or get_warehouse(settings)
    result = SyncResult(
        job=f"sync_financial_{statement_type}",
        started_at=time.time(),
    )

    file_map = {
        "balance": "balance_sheet.parquet",
        "income": "income.parquet",
        "cashflow": "cashflow.parquet",
    }
    file_name = file_map.get(statement_type)
    if file_name is None:
        result.errors.append(f"invalid statement_type: {statement_type}")
        result.finished_at = time.time()
        return result

    adapter_obj = adapter or _get_adapter_safe()
    if codes is None:
        try:
            codes = list(adapter_obj.get_code_list("EXTRA_STOCK_A_SH_SZ"))
        except Exception as e:
            result.errors.append(f"code list fetch failed: {e}")
            result.finished_at = time.time()
            return result

    codes = list(codes)
    result.total = len(codes)
    all_dfs: List[pd.DataFrame] = []

    for start in range(0, len(codes), batch_size):
        batch = codes[start : start + batch_size]
        batch_label = f"{start + 1}-{start + len(batch)}"
        try:
            df = adapter_obj.get_financial(
                codes=",".join(batch),
                statement_type=statement_type,
            )
            if df is not None and not df.empty:
                all_dfs.append(df)
                result.succeeded += len(batch)
                result.rows += len(df)
            else:
                result.skipped += len(batch)
        except Exception as e:
            result.failed += len(batch)
            result.errors.append(f"batch {batch_label}: {e}")

    if all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)
        # Normalize column names to lowercase snake_case for consistency
        combined.columns = [str(c).lower().strip() for c in combined.columns]
        # Ensure ts_code exists
        if "ts_code" not in combined.columns and "code" in combined.columns:
            combined = combined.rename(columns={"code": "ts_code"})
        path = _persist_reference(combined, warehouse.root / "reference" / file_name)
        result.success = path is not None
        warehouse.refresh_views()
    else:
        result.success = result.failed == 0

    result.finished_at = time.time()
    logger.info(
        "sync_financial(%s) total=%d succeeded=%d skipped=%d failed=%d rows=%d duration=%.2fs",
        statement_type,
        result.total,
        result.succeeded,
        result.skipped,
        result.failed,
        result.rows,
        result.duration,
    )
    return result


def sync_shareholder(
    *,
    codes: Optional[Sequence[str]] = None,
    batch_size: int = 50,
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Sync shareholder number data to the warehouse reference table."""
    settings = settings or get_settings()
    warehouse = warehouse or get_warehouse(settings)
    result = SyncResult(job="sync_shareholder", started_at=time.time())

    adapter_obj = adapter or _get_adapter_safe()
    if codes is None:
        try:
            codes = list(adapter_obj.get_code_list("EXTRA_STOCK_A_SH_SZ"))
        except Exception as e:
            result.errors.append(f"code list fetch failed: {e}")
            result.finished_at = time.time()
            return result

    codes = list(codes)
    result.total = len(codes)
    all_dfs: List[pd.DataFrame] = []

    for start in range(0, len(codes), batch_size):
        batch = codes[start : start + batch_size]
        batch_label = f"{start + 1}-{start + len(batch)}"
        try:
            df = adapter_obj.get_shareholder(codes=",".join(batch))
            if df is not None and not df.empty:
                all_dfs.append(df)
                result.succeeded += len(batch)
                result.rows += len(df)
            else:
                result.skipped += len(batch)
        except Exception as e:
            result.failed += len(batch)
            result.errors.append(f"batch {batch_label}: {e}")

    if all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)
        combined.columns = [str(c).lower().strip() for c in combined.columns]
        if "ts_code" not in combined.columns and "code" in combined.columns:
            combined = combined.rename(columns={"code": "ts_code"})
        path = _persist_reference(combined, warehouse.root / "reference" / "stk_holdernumber.parquet")
        result.success = path is not None
        warehouse.refresh_views()
    else:
        result.success = result.failed == 0

    result.finished_at = time.time()
    logger.info(
        "sync_shareholder total=%d succeeded=%d skipped=%d failed=%d rows=%d duration=%.2fs",
        result.total,
        result.succeeded,
        result.skipped,
        result.failed,
        result.rows,
        result.duration,
    )
    return result


# Common index codes to sync. Can be overridden via INDEX_CODES env var.
_DEFAULT_INDEX_CODES = [
    "000001.SH",  # 上证指数
    "000016.SH",  # 上证50
    "000300.SH",  # 沪深300
    "000905.SH",  # 中证500
    "399001.SZ",  # 深证成指
    "399006.SZ",  # 创业板指
    "399005.SZ",  # 中小板指
    "000688.SH",  # 科创50
    "899050.BJ",  # 北证50
]


def sync_index_component(
    *,
    index_codes: Optional[Sequence[str]] = None,
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Sync index constituent data to the warehouse reference table."""
    settings = settings or get_settings()
    warehouse = warehouse or get_warehouse(settings)
    result = SyncResult(job="sync_index_component", started_at=time.time())

    if index_codes is None:
        env_codes = os.environ.get("INDEX_CODES", "")
        index_codes = [c.strip() for c in env_codes.split(",") if c.strip()] or _DEFAULT_INDEX_CODES

    adapter_obj = adapter or _get_adapter_safe()
    result.total = len(index_codes)
    all_dfs: List[pd.DataFrame] = []

    for idx_code in index_codes:
        try:
            df = adapter_obj.get_index_component(index_code=idx_code)
            if df is not None and not df.empty:
                if "index_code" not in df.columns:
                    df["index_code"] = idx_code
                all_dfs.append(df)
                result.succeeded += 1
                result.rows += len(df)
            else:
                result.skipped += 1
        except Exception as e:
            result.failed += 1
            result.errors.append(f"{idx_code}: {e}")

    if all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)
        combined.columns = [str(c).lower().strip() for c in combined.columns]
        # Normalize con_code / code to con_code
        if "con_code" not in combined.columns and "code" in combined.columns:
            combined = combined.rename(columns={"code": "con_code"})
        path = _persist_reference(combined, warehouse.root / "reference" / "index_member.parquet")
        result.success = path is not None
        warehouse.refresh_views()
    else:
        result.success = result.failed == 0

    result.finished_at = time.time()
    logger.info(
        "sync_index_component total=%d succeeded=%d skipped=%d failed=%d rows=%d duration=%.2fs",
        result.total,
        result.succeeded,
        result.skipped,
        result.failed,
        result.rows,
        result.duration,
    )
    return result



def _run_sync_financial() -> None:
    try:
        for statement_type in ("balance", "income", "cashflow"):
            result = sync_financial(statement_type=statement_type)
            logger.info("scheduled sync_financial(%s): success=%s rows=%s",
                        statement_type, result.success, result.rows)
    except Exception as e:
        logger.exception("scheduled sync_financial failed: %s", e)


def _run_sync_shareholder() -> None:
    try:
        result = sync_shareholder()
        logger.info("scheduled sync_shareholder: success=%s rows=%s",
                    result.success, result.rows)
    except Exception as e:
        logger.exception("scheduled sync_shareholder failed: %s", e)


def _run_sync_index_component() -> None:
    try:
        result = sync_index_component()
        logger.info("scheduled sync_index_component: success=%s rows=%s",
                    result.success, result.rows)
    except Exception as e:
        logger.exception("scheduled sync_index_component failed: %s", e)
