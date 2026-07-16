"""Sync jobs for the L3 historical data warehouse (worker-side).

Runs inside the ``amazingdata_worker`` process — the only process that
holds a data-source SDK session. The module implements:

* ``sync_kline_daily`` / ``sync_kline_weekly`` / ``sync_kline_monthly`` — pull
  K-line data via the :class:`~amazingdata_worker.adapters.base.DataSourceAdapter`
  and overwrite the per-stock Parquet file (one file per code with all
  years merged).
* ``sync_meta_codes`` / ``sync_meta_calendar`` — refresh the global meta
  Parquet files used by DuckDB views.
* ``sync_financial`` / ``sync_shareholder`` / ``sync_index_component`` —
  refresh the reference Parquet tables.
* :class:`SyncResult` — small data class describing the outcome of a run.
* :func:`init_scheduler` / :func:`start_scheduler` / :func:`shutdown_scheduler`
  — APScheduler glue for the worker main loop.

All data-source access goes through the adapter protocol; the warehouse
schema and persistence helpers come from :mod:`adshare.historical`.
"""

from __future__ import annotations

import subprocess
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
    _filter_sh_sz_codes,
    _financial_dedup_keys,
    _is_sh_sz_code,
    _normalize_financial_df,
    kline_file_path,
    normalize_period,
    standardize_calendar_df,
    standardize_codes_df,
    standardize_kline_df,
    validate_kline_df,
    write_metadata,
)
from adshare.historical.warehouse import HistoricalWarehouse, get_warehouse

from amazingdata_worker.adapters.base import DataSourceAdapter

logger = get_logger(__name__)

# Default earliest date the sync job will pull from. Mirrors the
# ``default_begin_date`` constant in :mod:`adshare.core.config`.
_DEFAULT_BEGIN_DATE = 20200101


def _load_codes_from_meta(
    warehouse: Optional[HistoricalWarehouse] = None,
    settings: Optional[Settings] = None,
) -> Optional[List[str]]:
    """Load the A-share code list from the cached ``meta/codes.parquet``.

    Returns ``None`` if the file is missing or empty.  Reference sync jobs
    use this cache as their primary code source so they do not depend on the
    SDK's ``BaseData.get_code_list`` / ``get_code_info`` calls, which we have
    observed returning ``None`` or raising ``'NoneType' object is not
    subscriptable`` when the SDK session is under pressure.
    """
    try:
        settings = settings or get_settings()
        warehouse = warehouse or get_warehouse(settings)
        path = warehouse.meta_dir() / "codes.parquet"
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        if df is None or df.empty or "code" not in df.columns:
            return None
        codes = df["code"].dropna().astype(str).tolist()
        return [c for c in codes if _is_sh_sz_code(c)]
    except Exception as e:
        logger.warning("Failed to load codes from meta/codes.parquet: %s", e)
        return None


# ---------------------------------------------------------------------------
# SDK GIL protection
# ---------------------------------------------------------------------------
# The AmazingData C extension crashes with
#   "PyEval_SaveThread: the function must be called with the GIL held, but the
#    GIL is released (the current Python thread state is NULL)"
# when ``query_kline`` / ``SubscribeData`` is called from multiple OS threads
# concurrently (issue observed after commit 143ff75 attempted the same fix
# for ``SubscribeData``). We serialize **SDK calls** with a process-wide lock
# while keeping file I/O (Parquet write/read) outside the critical section
# so persistence still benefits from the thread pool.
_SDK_CALL_LOCK = threading.Lock()


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

def _get_adapter_safe() -> DataSourceAdapter:
    """Return the process-local data-source adapter."""
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
    """Standardize, validate and merge with any existing Parquet file.

    Incremental sync pulls only the recent window from the SDK; without
    merging with the on-disk history the parquet file gets overwritten
    with just the new window. We dedupe on ``(code, date)`` so a
    full-history pull followed by an incremental pull leaves the full
    history intact.
    """
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

    # Merge with any existing on-disk history for this code.
    if file_path.exists():
        try:
            existing = pd.read_parquet(file_path)
            if not existing.empty:
                std = pd.concat([existing, std], ignore_index=True)
                # Dedupe on the natural key (code, date). Prefer the
                # newer ``sync_at`` if a row appears in both.
                if "sync_at" in std.columns:
                    std = std.sort_values("sync_at").drop_duplicates(
                        subset=[c for c in ("code", "date") if c in std.columns],
                        keep="last",
                    )
                else:
                    std = std.drop_duplicates(
                        subset=[c for c in ("code", "date") if c in std.columns],
                        keep="last",
                    )
                std = std.sort_values("date").reset_index(drop=True)
        except Exception:
            # If the existing file is corrupt, just overwrite with the
            # new data.
            pass

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
            with _SDK_CALL_LOCK:
                codes = _filter_sh_sz_codes(
                    adapter_obj.get_code_list("EXTRA_STOCK_A_SH_SZ")
                )
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
                # The batch path is already sequential, but realtime threads may
                # also call into the SDK, so we still take the lock to keep
                # the SDK in a single-threaded state at all times.
                with _SDK_CALL_LOCK:
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
            # Serialize the SDK C-extension call across worker threads to
            # avoid the fatal GIL crash from concurrent ``query_kline`` calls.
            # File I/O (``_persist_kline``) stays outside the lock.
            with _SDK_CALL_LOCK:
                try:
                    df = adapter_obj.get_kline(
                        codes=code,
                        begin_date=begin_date,
                        end_date=end_date,
                        period=period,
                    )
                except Exception as e:  # noqa: BLE001
                    err_str = str(e).lower()
                    if "exceed the max limitation" in err_str or "rate limit" in err_str:
                        time.sleep(0.5 * (attempt + 1))
                    if attempt == attempts - 1:
                        return code, "failed", None, str(e)
                    time.sleep(0.2 * (attempt + 1))
                    continue
            path = _persist_kline(df, period, code, root)
            if path is None:
                return code, "skipped", None, None
            return code, "written", path, None
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
    # Default to the configured batch size so we hit the safe sequential
    # batch path. ``batch_size=1`` falls through to a per-code thread pool
    # which has historically crashed the AmazingData C extension under
    # concurrent load (see ``_SDK_CALL_LOCK``).
    if batch_size is None and codes is None:
        cfg = settings or get_settings()
        batch_size = int(cfg.max_codes_per_query)
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
    if batch_size is None and codes is None:
        cfg = settings or get_settings()
        batch_size = int(cfg.max_codes_per_query)
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
    if batch_size is None and codes is None:
        cfg = settings or get_settings()
        batch_size = int(cfg.max_codes_per_query)
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

    # Avoid hanging the scheduler on SDK calls that have been observed to
    # block indefinitely when the session is under pressure.  If the cached
    # code list is reasonably fresh, reuse it.
    codes_path = warehouse.meta_dir() / "codes.parquet"
    max_age_seconds = 3 * 24 * 3600  # 3 days
    try:
        if codes_path.exists() and (time.time() - codes_path.stat().st_mtime) <= max_age_seconds:
            cached_codes = _load_codes_from_meta(warehouse=warehouse, settings=settings)
            if cached_codes:
                logger.info(
                    "sync_meta_codes: codes file is fresh (%d codes), skipping SDK call",
                    len(cached_codes),
                )
                # Preserve the full cached file (including names) instead of
                # re-creating it from codes only, which would wipe names.
                cached_df = pd.read_parquet(codes_path)
                std = standardize_codes_df(cached_df)
                path = _persist_meta(std, codes_path)
                result.success = path is not None
                result.rows = len(std) if std is not None else 0
                result.finished_at = time.time()
                return result
    except Exception as e:
        logger.warning("sync_meta_codes: freshness check failed: %s", e)

    try:
        adapter_obj = adapter or _get_adapter_safe()
        raw: Optional[pd.DataFrame] = None
        try:
            with _SDK_CALL_LOCK:
                raw = adapter_obj.get_code_info(security_type="EXTRA_STOCK_A")
                if raw is None or (hasattr(raw, "empty") and raw.empty):
                    raw = adapter_obj.get_stock_basic(summary_only=False)
        except Exception as e:
            logger.warning("SDK code info fetch failed: %s; using cached codes", e)
            cached_codes = _load_codes_from_meta(warehouse=warehouse, settings=settings)
            if cached_codes:
                raw = pd.DataFrame({"code": cached_codes})
            else:
                raise

        if raw is not None and not (hasattr(raw, "empty") and raw.empty):
            # Drop Beijing Stock Exchange rows: we no longer store or
            # serve them in the L3 warehouse.
            if "code" in raw.columns:
                before = len(raw)
                raw = raw[raw["code"].astype(str).apply(_is_sh_sz_code)]
                dropped = before - len(raw)
                if dropped:
                    logger.info(
                        "sync_meta_codes: filtered out %d .BJ rows from SDK result",
                        dropped,
                    )
        std = standardize_codes_df(raw)
        path = _persist_meta(std, warehouse.meta_dir() / "codes.parquet")
        result.success = path is not None
        result.rows = len(std) if std is not None else 0
        # If the SDK did not provide a list_date for any row, log a
        # warning so the operator notices the metadata gap. We do not
        # fail because the AmazingData SDK's get_code_info does not
        # always populate list_date and the row is still useful.
        if std is not None and not std.empty and "list_date" in std.columns:
            missing = int((std["list_date"] == 0).sum())
            if missing == len(std):
                logger.warning(
                    "sync_meta_codes: SDK returned no list_date values "
                    "for any of %d codes; known limitation, list_date "
                    "stays as 0",
                    len(std),
                )
            elif missing:
                logger.info(
                    "sync_meta_codes: %d/%d codes have no list_date "
                    "(likely new listings)",
                    missing, len(std),
                )
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
        with _SDK_CALL_LOCK:
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
            # Reference data sync (weekly) — run directly in scheduler thread
            # with SDK call lock protection (single-process architecture).
            # NOTE: financial statement sync is disabled; balance/income/cashflow
            # data is not used and its HDF5 cache consumes several GB.
            # scheduler.add_job(
            #     _run_sync_financial,
            #     "cron",
            #     day_of_week="sat",
            #     hour=2,
            #     minute=0,
            #     id="sync_financial",
            #     replace_existing=True,
            # )
            scheduler.add_job(
                _run_sync_shareholder,
                "cron",
                day_of_week=settings.sync_shareholder_day_of_week,
                hour=int(settings.sync_shareholder_hour),
                minute=int(settings.sync_shareholder_minute),
                id="sync_shareholder",
                replace_existing=True,
            )
            scheduler.add_job(
                _run_sync_index_component,
                "cron",
                day_of_week=settings.sync_index_component_day_of_week,
                hour=int(settings.sync_index_component_hour),
                minute=int(settings.sync_index_component_minute),
                id="sync_index_component",
                replace_existing=True,
            )

        # Maintenance (idempotent repair) — disabled by default. When
        # enabled, the routines run weekly as a defensive cleanup
        # after the regular sync jobs have settled. They are no-ops
        # on already-clean data, so a missed run is harmless.
        if settings.maintenance_schedule_enabled:
            scheduler.add_job(
                _run_repair_kline,
                "cron",
                day_of_week=settings.maintenance_kline_day_of_week,
                hour=int(settings.maintenance_kline_hour),
                minute=int(settings.maintenance_kline_minute),
                id="repair_kline_weekly",
                replace_existing=True,
            )
            scheduler.add_job(
                _run_repair_financial,
                "cron",
                day_of_week=settings.maintenance_financial_day_of_week,
                hour=int(settings.maintenance_financial_hour),
                minute=int(settings.maintenance_financial_minute),
                id="repair_financial_weekly",
                replace_existing=True,
            )
            logger.info(
                "maintenance schedule enabled: kline=%s %02d:%02d, "
                "financial=%s %02d:%02d",
                settings.maintenance_kline_day_of_week,
                int(settings.maintenance_kline_hour),
                int(settings.maintenance_kline_minute),
                settings.maintenance_financial_day_of_week,
                int(settings.maintenance_financial_hour),
                int(settings.maintenance_financial_minute),
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
        _sync_to_remote()
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
        _sync_to_remote()
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
        _sync_to_remote()
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_kline_monthly failed: %s", e)


def _sync_to_remote() -> None:
    """Push updated L3 data to the remote adshare-api server.

    Runs after a scheduled kline sync completes.  The companion script
    lives at ``scripts/sync_to_remote.sh`` and is shared with manual runs.
    """
    project_root = Path(__file__).resolve().parents[1]
    script = project_root / "scripts" / "sync_to_remote.sh"
    if not script.exists():
        logger.warning("sync_to_remote: script not found at %s", script)
        return
    logger.info("sync_to_remote: starting %s", script)
    try:
        result = subprocess.run(
            ["/bin/bash", str(script)],
            capture_output=True,
            text=True,
            check=False,
            timeout=1800,
        )
        if result.returncode == 0:
            logger.info("sync_to_remote: success\n%s", result.stdout)
        else:
            logger.error(
                "sync_to_remote: failed (exit %s)\nstdout:\n%s\nstderr:\n%s",
                result.returncode,
                result.stdout,
                result.stderr,
            )
    except Exception as e:
        logger.exception("sync_to_remote: error running script: %s", e)


def _run_sync_meta_codes() -> None:
    try:
        sync_meta_codes()
        _sync_to_remote()
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_meta_codes failed: %s", e)


def _run_sync_meta_calendar() -> None:
    try:
        sync_meta_calendar()
        _sync_to_remote()
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_meta_calendar failed: %s", e)


# ----------------------------------------------------------------------
# Maintenance (idempotent L3 warehouse repair) scheduler wrappers
# ----------------------------------------------------------------------

def _run_repair_kline() -> None:
    """Run the K-line + codes repair routine on a schedule.

    Idempotent: skips the rewrite when nothing changed, so this is
    safe to run as a defensive cron after every sync window.
    """
    try:
        from adshare.historical.maintenance import (
            repair_kline_directory,
            repair_codes_table,
        )
        settings = get_settings()
        warehouse = get_warehouse(settings)
        r1 = repair_kline_directory(dry_run=False, warehouse=warehouse)
        r2 = repair_codes_table(dry_run=False, warehouse=warehouse)
        logger.info(
            "scheduled maintenance: kline %s | codes %s",
            r1.summary(), r2.summary(),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled repair_kline failed: %s", e)


def _run_repair_financial() -> None:
    """Run the financial-table repair routine on a schedule.

    Idempotent: skips the rewrite when nothing changed.
    """
    try:
        from adshare.historical.maintenance import repair_financial_table
        settings = get_settings()
        warehouse = get_warehouse(settings)
        r = repair_financial_table(dry_run=False, warehouse=warehouse)
        logger.info("scheduled maintenance: financial %s", r.summary())
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled repair_financial failed: %s", e)



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
    offset: int = 0,
    merge: bool = True,
    settings: Optional[Settings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Sync financial statement data to the warehouse reference table.

    Args:
        statement_type: "balance", "income", or "cashflow".
        codes: Optional list of codes. Defaults to all A-share stocks.
        batch_size: Number of codes per SDK call.
        offset: Start index in the codes list (for resume).
        merge: If True, merge with existing parquet instead of overwriting.
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
        codes = _load_codes_from_meta(warehouse=warehouse, settings=settings)
        if codes is None:
            try:
                with _SDK_CALL_LOCK:
                    codes = _filter_sh_sz_codes(
                        adapter_obj.get_code_list("EXTRA_STOCK_A_SH_SZ")
                    )
            except Exception as e:
                result.errors.append(f"code list fetch failed: {e}")
                result.finished_at = time.time()
                return result

    codes = list(codes)
    result.total = len(codes)
    all_dfs: List[pd.DataFrame] = []

    # Slice codes according to offset
    codes = codes[offset:]
    result.total = len(codes) + offset

    for start in range(0, len(codes), batch_size):
        batch = codes[start : start + batch_size]
        batch_label = f"{offset + start + 1}-{offset + start + len(batch)}"
        try:
            with _SDK_CALL_LOCK:
                df = adapter_obj.get_financial(
                    codes=",".join(batch),
                    statement_type=statement_type,
                    begin_date=20200101,
                    end_date=int(datetime.now().strftime("%Y%m%d")),
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

        # Fix the report_type enum (SDK sometimes returns a date string)
        combined = _normalize_financial_df(combined, statement_type)

        target_path = warehouse.root / "reference" / file_name
        if merge and target_path.exists():
            # Read existing and merge
            try:
                existing = pd.read_parquet(target_path)
                # Apply the same normalization to the on-disk frame so
                # any historical bad report_type rows get cleaned up
                # when we re-write the merged file.
                existing = _normalize_financial_df(existing, statement_type)
                combined = pd.concat([existing, combined], ignore_index=True)
                # Drop duplicates using the natural key for financial
                # statements: (ts_code, reporting_period, report_type,
                # statement_type, comp_type_code). The SDK can return
                # multiple versions of the same period (合并/母公司/调整
                # 报表) and we want to keep all of them, deduping only
                # exact duplicates introduced by a re-pull.
                dup_cols = _financial_dedup_keys(combined)
                if dup_cols:
                    before = len(combined)
                    combined = combined.drop_duplicates(subset=dup_cols, keep="last")
                    dropped = before - len(combined)
                    if dropped:
                        logger.info(
                            "sync_financial(%s): dedup dropped %d exact-duplicate rows",
                            statement_type, dropped,
                        )
            except Exception as e:
                logger.warning("Merge failed for %s: %s", file_name, e)

        path = _persist_reference(combined, target_path)
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
        codes = _load_codes_from_meta(warehouse=warehouse, settings=settings)
        if codes is None:
            try:
                with _SDK_CALL_LOCK:
                    codes = _filter_sh_sz_codes(
                        adapter_obj.get_code_list("EXTRA_STOCK_A_SH_SZ")
                    )
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
            with _SDK_CALL_LOCK:
                df = adapter_obj.get_shareholder(
                    codes=",".join(batch),
                    begin_date=20200101,
                    end_date=int(datetime.now().strftime("%Y%m%d")),
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
# Beijing Stock Exchange indices (e.g. 899050.BJ 北证50) are excluded —
# the warehouse no longer serves BSE data.
_DEFAULT_INDEX_CODES = [
    "000001.SH",  # 上证指数
    "000016.SH",  # 上证50
    "000300.SH",  # 沪深300
    "000905.SH",  # 中证500
    "399001.SZ",  # 深证成指
    "399006.SZ",  # 创业板指
    "399005.SZ",  # 中小板指
    "000688.SH",  # 科创50
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
        env_codes = settings.index_codes
        index_codes = [c.strip() for c in env_codes.split(",") if c.strip()] or _DEFAULT_INDEX_CODES

    adapter_obj = adapter or _get_adapter_safe()
    result.total = len(index_codes)
    all_dfs: List[pd.DataFrame] = []

    for idx_code in index_codes:
        try:
            with _SDK_CALL_LOCK:
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
        # Drop BSE constituents: the warehouse only serves SH/SZ
        if "con_code" in combined.columns:
            before = len(combined)
            combined = combined[
                combined["con_code"].astype(str).apply(_is_sh_sz_code)
            ]
            dropped = before - len(combined)
            if dropped:
                logger.info(
                    "sync_index_component: filtered out %d .BJ constituent rows",
                    dropped,
                )
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
    """Run financial sync directly in scheduler thread with SDK lock."""
    try:
        settings = get_settings()
        warehouse = get_warehouse(settings)
        adapter = _get_adapter_safe()
        for statement_type in ("balance", "income", "cashflow"):
            logger.info("Starting financial sync: %s", statement_type)
            result = sync_financial(
                statement_type=statement_type,
                batch_size=50,
                settings=settings,
                warehouse=warehouse,
                adapter=adapter,
            )
            logger.info(
                "sync_financial(%s): success=%s rows=%s failed=%s duration=%.2fs",
                statement_type,
                result.success,
                result.rows,
                result.failed,
                result.duration,
            )
    except Exception as e:
        logger.exception("sync_financial failed: %s", e)


def _run_sync_shareholder() -> None:
    """Run shareholder sync directly in scheduler thread with SDK lock."""
    try:
        settings = get_settings()
        warehouse = get_warehouse(settings)
        adapter = _get_adapter_safe()
        result = sync_shareholder(
            batch_size=50,
            settings=settings,
            warehouse=warehouse,
            adapter=adapter,
        )
        logger.info(
            "sync_shareholder: success=%s rows=%s failed=%s duration=%.2fs",
            result.success,
            result.rows,
            result.failed,
            result.duration,
        )
    except Exception as e:
        logger.exception("sync_shareholder failed: %s", e)


def _run_sync_index_component() -> None:
    """Run index component sync directly in scheduler thread with SDK lock."""
    try:
        settings = get_settings()
        warehouse = get_warehouse(settings)
        adapter = _get_adapter_safe()
        result = sync_index_component(
            settings=settings,
            warehouse=warehouse,
            adapter=adapter,
        )
        logger.info(
            "sync_index_component: success=%s rows=%s failed=%s duration=%.2fs",
            result.success,
            result.rows,
            result.failed,
            result.duration,
        )
    except Exception as e:
        logger.exception("sync_index_component failed: %s", e)
