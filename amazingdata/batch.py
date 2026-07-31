"""盘后模式: APScheduler 定时同步 -> PostgreSQL.

启动:
    python -m amazingdata.batch

镜像:
    amazingdata-batch  (FROM amazingdata-base)

Docker:
    docker compose -f amazingdata/docker-compose.batch.yml up -d

职责:
- 登录 AmazingData SDK
- 初始化 PostgreSQL repository（连接池 + schema migration）
- 启动 APScheduler，按 cron 跑 K线/meta/参考数据同步任务
- 阻塞主循环，按 SIGTERM/SIGINT 优雅退出

TGW 单连接账户约束:
- 此服务独占一个 SDK 会话
- 同一主机上 realtime 服务的 SDK 会话必须互斥（通过外部调度切换容器）

数据范围:
- 仅 SH/SZ A 股（主板/创业板/科创板，不含北交所）
- K线: daily/weekly/monthly，一股票一文件，全部历史合并
- Meta: codes / calendar
- Reference: shareholder / index_component（financial 已禁用）
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional, Sequence

# Allow running as ``python amazingdata/batch.py``
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from amazingdata.config import WorkerSettings, get_worker_settings  # noqa: E402
from adshare.core.logging import setup_logging, get_logger  # noqa: E402
from adshare.historical.models import (  # noqa: E402
    _filter_sh_sz_codes,
    _is_sh_sz_code,
    _normalize_financial_df,
    normalize_period,
    standardize_calendar_df,
    standardize_codes_df,
    standardize_kline_df,
    validate_kline_df,
)
from adshare.historical.warehouse import HistoricalWarehouse, get_warehouse  # noqa: E402

from amazingdata.adapters.amazingdata import get_adapter  # noqa: E402
from amazingdata.adapters.base import DataSourceAdapter  # noqa: E402
from amazingdata.freshness import publish_kline_freshness  # noqa: E402
from amazingdata.heartbeat import HeartbeatWriter  # noqa: E402

try:
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED  # noqa: E402
except ImportError:  # pragma: no cover
    EVENT_JOB_EXECUTED = 4096
    EVENT_JOB_ERROR = 8192

logger = get_logger("amazingdata.batch")

_shutdown_event = threading.Event()


# Module-level job-run tracking — consumed by the heartbeat payload
# getter. Updated by the scheduler listener registered in
# ``init_scheduler``.
_last_job_lock = threading.Lock()
_last_job: dict = {
    "id": None,
    "status": None,
    "at": None,
    "error": None,
}


def _on_job_event(event) -> None:  # noqa: ANN001
    """Track the most recent scheduler job outcome for the heartbeat."""
    code = getattr(event, "code", None)
    is_error = code == EVENT_JOB_ERROR
    is_success = code == EVENT_JOB_EXECUTED and not getattr(event, "exception", None)
    if not (is_error or is_success):
        return
    exc = getattr(event, "exception", None)
    err_str = f"{type(exc).__name__}: {exc}" if exc else None
    with _last_job_lock:
        _last_job.update({
            "id": getattr(event, "job_id", None),
            "status": "ok" if is_success else "failed",
            "at": int(time.time()),
            "error": err_str if is_error else None,
        })


# ============================================================
# SDK login (with retry for TGW single-connection accounts)
# ============================================================

def _init_sdk_login(max_wait_seconds: float = 1800.0) -> bool:
    """Login to AmazingData SDK with exponential backoff."""
    adapter = get_adapter()
    deadline = time.time() + max_wait_seconds
    delay = 5.0
    while time.time() < deadline:
        try:
            if adapter.login():
                logger.info("AmazingData login successful: %s", adapter.login_info)
                return True
            logger.error("AmazingData login failed, will retry in %.1fs", delay)
        except Exception as e:
            logger.error("AmazingData login error: %s, will retry in %.1fs", e, delay)
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, 60.0)
    logger.error("Failed to login to AmazingData within %.0fs", max_wait_seconds)
    return False


# ============================================================
# Code list loader (cached) and GIL protection
# ============================================================

_DEFAULT_BEGIN_DATE = 20200101
_FACTOR_HISTORY_BEGIN_DATE = 19900101
_DEFAULT_INDEX_CODES = ["000300.SH", "000905.SH", "000016.SH", "000688.SH"]

# The AmazingData C extension crashes with
#   "PyEval_SaveThread: the function must be called with the GIL held, ..."
# when ``query_kline`` / ``SubscribeData`` is called from multiple OS threads
# concurrently. Serialize SDK calls with a process-wide lock; file I/O
# (Parquet write/read) stays outside the critical section.
_SDK_CALL_LOCK = threading.Lock()


def _load_codes_from_meta(
    warehouse: Optional[HistoricalWarehouse] = None,
    settings: Optional[WorkerSettings] = None,
) -> Optional[List[str]]:
    """Load the A-share code list from PostgreSQL.

    Returns ``None`` if the file is missing or empty. Reference sync jobs
    use this cache as their primary code source so they do not depend on
    the SDK's ``BaseData.get_code_list`` / ``get_code_info`` calls, which
    have been observed returning ``None`` or raising ``'NoneType' object
    is not subscriptable`` when the SDK session is under pressure.
    """
    try:
        settings = settings or get_worker_settings()
        warehouse = warehouse or get_warehouse(settings)
        df = warehouse.query_codes()
        if df is None or df.empty or "code" not in df.columns:
            return None
        codes = df["code"].dropna().astype(str).tolist()
        return [c for c in codes if _is_sh_sz_code(c)]
    except Exception as e:
        logger.warning("Failed to load codes from PostgreSQL: %s", e)
        return None


def _get_adapter_safe() -> DataSourceAdapter:
    """Return the process-local data-source adapter."""
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


def _persist_kline(
    df: pd.DataFrame,
    period: str,
    code: str,
    warehouse: HistoricalWarehouse,
) -> int:
    """Standardize, validate and upsert one security into PostgreSQL."""
    if df is None or df.empty:
        return 0
    std = standardize_kline_df(df, code=code)
    std = validate_kline_df(std)
    if std.empty:
        return 0
    return warehouse.upsert_kline(_ensure_code_suffix(code), period, std)


def _date_bounds(
    from_date: Optional[int],
    to_date: Optional[int],
    today: Optional[datetime] = None,
) -> tuple[int, int]:
    """Resolve inclusive (begin, end) date integers for a sync run."""
    today = today or datetime.now()
    end_default = int(today.strftime("%Y%m%d"))
    begin = int(from_date) if from_date is not None else _DEFAULT_BEGIN_DATE
    end = int(to_date) if to_date is not None else end_default
    if begin > end:
        begin, end = end, begin
    return begin, end


# ============================================================
# Result helpers
# ============================================================

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


def _start_sync_job(
    warehouse: HistoricalWarehouse,
    result: SyncResult,
    *,
    data_type: str,
    sync_mode: str = "INCREMENTAL",
) -> dict:
    """Insert a ``RUNNING`` row for the sync and return a context dict.

    The returned dict has two keys:

    * ``job_id`` — the ``market.sync_job.id`` (or ``None`` if the insert
      failed; callers can still proceed, the finalize step will fall
      back to a direct INSERT).
    * ``finalize`` — a callable the caller invokes once the sync has
      finished. It accepts optional overrides for any completion field;
      the defaults are derived from ``result``.
    """
    job_id = warehouse.start_sync_job(
        job_name=result.job, data_type=data_type, sync_mode=sync_mode,
    )
    if job_id is None:
        logger.warning(
            "sync_job start row not created for %s/%s; "
            "completion will fall back to direct INSERT",
            result.job, data_type,
        )
    return {
        "job_id": job_id,
        "finalized": False,
        "payload": _build_sync_completion_payload(
            result, data_type=data_type,
        ),
    }


def _finalize_sync_job(
    ctx: dict,
    warehouse: HistoricalWarehouse,
    result: SyncResult,
    *,
    data_type: str,
    range_start: Optional[int] = None,
    range_end: Optional[int] = None,
) -> None:
    """Finalize the sync_job row (started by ``_start_sync_job``).

    Builds the completion payload (allowing ``range_start`` /
    ``range_end`` overrides), then either UPDATEs the existing row or
    INSERTs a new one if the start row never landed. Safe to call once;
    subsequent calls are no-ops.
    """
    if ctx.get("finalized"):
        return
    ctx["finalized"] = True
    payload = _build_sync_completion_payload(
        result, data_type=data_type,
        range_start=range_start, range_end=range_end,
    )
    try:
        warehouse.record_sync_job(job_id=ctx["job_id"], **payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to record sync job %s: %s", result.job, exc)


def _build_sync_completion_payload(
    result: SyncResult,
    *,
    data_type: str,
    range_start: Optional[int] = None,
    range_end: Optional[int] = None,
) -> dict:
    """Translate a SyncResult into a record_sync_job kwargs dict."""
    status = (
        "SUCCESS"
        if result.success
        else ("PARTIAL_SUCCESS" if result.succeeded else "FAILED")
    )
    return dict(
        job_name=result.job,
        data_type=data_type,
        status=status,
        range_start=range_start,
        range_end=range_end,
        records_read=result.rows,
        records_inserted=result.rows,
        records_failed=result.failed,
        started_at=result.started_at,
        error_message="\n".join(result.errors)[:10000] or None,
    )


# ============================================================
# Sync jobs
# ============================================================

def sync_kline(
    period: str = "day",
    *,
    from_date: Optional[int] = None,
    to_date: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    batch_size: Optional[int] = None,
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Generic K-line sync (daily/weekly/monthly)."""
    settings = settings or get_worker_settings()
    warehouse = warehouse or get_warehouse(settings)
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
            batch_df = None
            batch_error = None
            for attempt in range(attempts):
                try:
                    with _SDK_CALL_LOCK:
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
                written = _persist_kline(code_df, period, code, warehouse)
                if written <= 0:
                    result.skipped += 1
                    continue
                result.succeeded += 1
                rows_written += written

            logger.info(
                "sync_kline(%s) range=[%s,%s] batch=%s/%s succeeded=%d skipped=%d failed=%d rows=%d",
                period, begin_date, end_date,
                min(start + len(batch), len(codes)), len(codes),
                result.succeeded, result.skipped, result.failed, rows_written,
            )

        result.rows = rows_written
        result.finished_at = time.time()
        result.success = result.failed == 0
        _publish_period_freshness(period, warehouse, rows_written)
        _finalize_sync_job(
            _start_sync_job(
                warehouse, result,
                data_type=normalize_period(period).upper().replace("DAILY", "DAILY_BAR")
                .replace("WEEKLY", "WEEKLY_BAR")
                .replace("MONTHLY", "MONTHLY_BAR"),
            ),
            warehouse, result,
            data_type=normalize_period(period).upper().replace("DAILY", "DAILY_BAR")
            .replace("WEEKLY", "WEEKLY_BAR")
            .replace("MONTHLY", "MONTHLY_BAR"),
            range_start=begin_date,
            range_end=end_date,
        )
        logger.info(
            "sync_kline(%s) range=[%s,%s] succeeded=%d skipped=%d failed=%d rows=%d duration=%.2fs",
            period, begin_date, end_date,
            result.succeeded, result.skipped, result.failed, result.rows, result.duration,
        )
        return result

    def _sync_one(code: str) -> tuple[str, str, int, Optional[str]]:
        attempts = max(1, int(settings.sync_retry_attempts))
        for attempt in range(attempts):
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
                        return code, "failed", 0, str(e)
                    time.sleep(0.2 * (attempt + 1))
                    continue
            written = _persist_kline(df, period, code, warehouse)
            if written <= 0:
                return code, "skipped", 0, None
            return code, "written", written, None
        return code, "failed", 0, "unknown"

    rows_written = 0
    workers = max(1, int(settings.sync_workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_sync_one, c): c for c in codes}
        for fut in as_completed(futures):
            code, status, written, err = fut.result()
            if status == "written":
                result.succeeded += 1
                rows_written += written
            elif status == "skipped":
                result.skipped += 1
            else:
                result.failed += 1
                if err:
                    result.errors.append(f"{code}: {err}")

    result.rows = rows_written
    result.finished_at = time.time()
    result.success = result.failed == 0
    _publish_period_freshness(period, warehouse, rows_written)
    _finalize_sync_job(
        _start_sync_job(
            warehouse, result,
            data_type=normalize_period(period).upper().replace("DAILY", "DAILY_BAR")
            .replace("WEEKLY", "WEEKLY_BAR")
            .replace("MONTHLY", "MONTHLY_BAR"),
        ),
        warehouse, result,
        data_type=normalize_period(period).upper().replace("DAILY", "DAILY_BAR")
        .replace("WEEKLY", "WEEKLY_BAR")
        .replace("MONTHLY", "MONTHLY_BAR"),
        range_start=begin_date,
        range_end=end_date,
    )
    logger.info(
        "sync_kline(%s) range=[%s,%s] succeeded=%d failed=%d rows=%d duration=%.2fs",
        period, begin_date, end_date,
        result.succeeded, result.failed, result.rows, result.duration,
    )
    return result


def _publish_period_freshness(
    period: str,
    warehouse: HistoricalWarehouse,
    rows_written: int,
) -> None:
    """Publish PostgreSQL freshness metrics to Redis for the dashboard."""
    try:
        subdir = normalize_period(period)
        stats = warehouse.stats()["periods"].get(subdir, {})
        first_date = stats.get("first_date")
        last_date = stats.get("last_date")
        code_count = int(stats.get("code_count") or 0)
        try:
            published = publish_kline_freshness(
                period=period,
                code_count=code_count,
                rows_inserted=rows_written,
                first_date=first_date,
                last_date=last_date,
                last_sync_at=int(time.time()),
                historical_path="postgresql",
            )
            if published is None:
                logger.debug(
                    "freshness publish returned no row for period=%s last_date=%s",
                    period, last_date,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("freshness publish failed for %s: %s", period, e)
    except Exception as e:  # noqa: BLE001
        logger.warning("sync_kline: failed to publish freshness: %s", e)


def sync_kline_daily(
    *,
    year: Optional[int] = None,
    from_date: Optional[int] = None,
    to_date: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    batch_size: Optional[int] = None,
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    if year is not None and from_date is None and to_date is None:
        today = datetime.now()
        end_cap = int(today.strftime("%Y%m%d"))
        from_date = int(f"{int(year)}0101")
        to_date = min(end_cap, int(f"{int(year)}1231"))
    if batch_size is None and codes is None:
        cfg = settings or get_worker_settings()
        batch_size = int(cfg.max_codes_per_query)
    return sync_kline(
        "day", from_date=from_date, to_date=to_date,
        codes=codes, batch_size=batch_size,
        settings=settings, warehouse=warehouse, adapter=adapter,
    )


def sync_kline_weekly(
    *,
    year: Optional[int] = None,
    from_date: Optional[int] = None,
    to_date: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    batch_size: Optional[int] = None,
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    if year is not None and from_date is None and to_date is None:
        today = datetime.now()
        end_cap = int(today.strftime("%Y%m%d"))
        from_date = int(f"{int(year)}0101")
        to_date = min(end_cap, int(f"{int(year)}1231"))
    if batch_size is None and codes is None:
        cfg = settings or get_worker_settings()
        batch_size = int(cfg.max_codes_per_query)
    return sync_kline(
        "week", from_date=from_date, to_date=to_date,
        codes=codes, batch_size=batch_size,
        settings=settings, warehouse=warehouse, adapter=adapter,
    )


def sync_kline_monthly(
    *,
    year: Optional[int] = None,
    from_date: Optional[int] = None,
    to_date: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    batch_size: Optional[int] = None,
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    if year is not None and from_date is None and to_date is None:
        today = datetime.now()
        end_cap = int(today.strftime("%Y%m%d"))
        from_date = int(f"{int(year)}0101")
        to_date = min(end_cap, int(f"{int(year)}1231"))
    if batch_size is None and codes is None:
        cfg = settings or get_worker_settings()
        batch_size = int(cfg.max_codes_per_query)
    return sync_kline(
        "month", from_date=from_date, to_date=to_date,
        codes=codes, batch_size=batch_size,
        settings=settings, warehouse=warehouse, adapter=adapter,
    )


def _compress_factor_changes(factors: pd.DataFrame) -> pd.DataFrame:
    """Keep the first factor and dates where its stored value changes."""
    if factors is None or factors.empty:
        return pd.DataFrame(columns=["date", "adj_factor"])
    result = factors[["date", "adj_factor"]].copy()
    result["date"] = pd.to_numeric(result["date"], errors="coerce")
    result["adj_factor"] = pd.to_numeric(
        result["adj_factor"], errors="coerce"
    ).round(10)
    result = (
        result.dropna(subset=["date", "adj_factor"])
        .drop_duplicates("date", keep="last")
        .sort_values("date")
    )
    result["date"] = result["date"].astype(int)
    changed = result["adj_factor"].ne(result["adj_factor"].shift())
    return result.loc[changed, ["date", "adj_factor"]].reset_index(drop=True)


def _flag_is_true(value: Any) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _recent_factor_event_start(
    warehouse: HistoricalWarehouse,
    end_date: int,
    sessions: int,
) -> int:
    calendar = warehouse.query_calendar(
        market="SH", end_date=end_date
    )
    if not calendar.empty:
        trading = calendar[
            calendar["is_trading_day"].fillna(False).astype(bool)
        ]
        dates = (
            pd.to_numeric(trading["date"], errors="coerce")
            .dropna()
            .astype(int)
            .sort_values()
        )
        if not dates.empty:
            return int(dates.iloc[-max(1, int(sessions))])
    end = datetime.strptime(str(int(end_date)), "%Y%m%d")
    return int((end - timedelta(days=max(7, int(sessions) * 2))).strftime("%Y%m%d"))


def sync_adjustment_factors(
    *,
    from_date: Optional[int] = None,
    to_date: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    batch_size: Optional[int] = None,
    refresh: bool = True,
    event_driven: bool = False,
    event_lookback_sessions: int = 5,
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Refresh canonical sparse timelines in ``market.adjustment_factor``.

    AmazingData returns adjustment factors as a wide date-by-stock matrix.
    Requesting the whole market at once and stacking that matrix can require
    several gigabytes of transient memory, so factors are fetched in bounded
    stock batches. In event-driven mode only stocks with recent ex-right/
    ex-dividend flags (plus stocks missing a baseline) are requested.
    """
    settings = settings or get_worker_settings()
    warehouse = warehouse or get_warehouse(settings)
    adapter_obj = adapter or _get_adapter_safe()
    begin_date, end_date = _date_bounds(from_date, to_date)

    if codes is None:
        codes = _load_codes_from_meta(warehouse=warehouse, settings=settings)
    code_list = [_ensure_code_suffix(code) for code in (codes or [])]

    local_path = Path(settings.amazingdata_local_path)
    local_path.mkdir(parents=True, exist_ok=True)
    if event_driven and code_list:
        event_begin = _recent_factor_event_start(
            warehouse, end_date, event_lookback_sessions
        )
        try:
            with _SDK_CALL_LOCK:
                events = adapter_obj.get_adjustment_events(
                    codes=",".join(code_list),
                    begin_date=event_begin,
                    end_date=end_date,
                    local_path=str(local_path),
                    refresh=refresh,
                )
        except Exception as exc:  # noqa: BLE001
            result = SyncResult(
                job="sync_adjustment_factors",
                started_at=time.time(),
                finished_at=time.time(),
                success=False,
                total=len(code_list),
                failed=len(code_list),
                errors=[f"adjustment event detection failed: {exc}"],
            )
            _finalize_sync_job(
                _start_sync_job(
                    warehouse, result, data_type="ADJUSTMENT_FACTOR"
                ),
                warehouse,
                result,
                data_type="ADJUSTMENT_FACTOR",
                range_start=event_begin,
                range_end=end_date,
            )
            return result

        affected: set[str] = set()
        if isinstance(events, pd.DataFrame) and not events.empty:
            for record in events.to_dict("records"):
                if _flag_is_true(record.get("is_ex_right")) or _flag_is_true(
                    record.get("is_ex_dividend")
                ):
                    affected.add(_ensure_code_suffix(str(record.get("code", ""))))
        missing = set(
            warehouse.query_codes_without_adjustment_factors(code_list)
        )
        code_list = sorted((affected | missing) & set(code_list))
        logger.info(
            "adjustment factor event scan range=[%s,%s] affected=%d "
            "missing_baseline=%d selected=%d",
            event_begin,
            end_date,
            len(affected),
            len(missing),
            len(code_list),
        )
        # An affected stock is rebuilt from its full vendor timeline so a
        # historical correction can remove obsolete change points as well.
        begin_date = _FACTOR_HISTORY_BEGIN_DATE

    result = SyncResult(
        job="sync_adjustment_factors",
        started_at=time.time(),
        total=len(code_list),
    )
    if not code_list:
        result.success = True
        result.finished_at = time.time()
        _finalize_sync_job(
            _start_sync_job(
                warehouse, result, data_type="ADJUSTMENT_FACTOR"
            ),
            warehouse,
            result,
            data_type="ADJUSTMENT_FACTOR",
            range_start=begin_date,
            range_end=end_date,
        )
        return result

    factor_batch_size = max(
        1, int(batch_size or settings.max_codes_per_query)
    )
    required = {"code", "date", "adj_factor"}

    for start in range(0, len(code_list), factor_batch_size):
        batch = code_list[start : start + factor_batch_size]
        batch_label = f"{start + 1}-{start + len(batch)}"
        try:
            with _SDK_CALL_LOCK:
                factors = adapter_obj.get_adjustment_factors(
                    codes=",".join(batch),
                    begin_date=begin_date,
                    end_date=end_date,
                    local_path=str(local_path),
                    refresh=refresh,
                )
        except Exception as exc:  # noqa: BLE001
            result.failed += len(batch)
            result.errors.append(f"batch {batch_label}: {exc}")
            continue

        if not isinstance(factors, pd.DataFrame) or not required <= set(factors.columns):
            result.failed += len(batch)
            result.errors.append(
                f"batch {batch_label}: adjustment factor source returned "
                "no canonical factor data"
            )
            continue

        factors = factors.copy()
        factors["code"] = factors["code"].astype(str)
        factors["date"] = pd.to_numeric(factors["date"], errors="coerce")
        factors["adj_factor"] = pd.to_numeric(
            factors["adj_factor"], errors="coerce"
        )
        factors = factors.dropna(subset=["date", "adj_factor"])
        factors["date"] = factors["date"].astype(int)

        for code in batch:
            try:
                code_factors = (
                    factors[factors["code"] == code][["date", "adj_factor"]]
                )
                sparse_factors = _compress_factor_changes(code_factors)
                if sparse_factors.empty:
                    result.skipped += 1
                    continue
                written = warehouse.replace_adjustment_factor_timeline(
                    code, sparse_factors
                )
                if written <= 0:
                    result.skipped += 1
                    continue
                result.succeeded += 1
                result.rows += written
            except Exception as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append(f"{code}: {exc}")

        logger.info(
            "sync_adjustment_factors range=[%s,%s] batch=%s/%s "
            "succeeded=%d skipped=%d failed=%d rows=%d",
            begin_date,
            end_date,
            min(start + len(batch), len(code_list)),
            len(code_list),
            result.succeeded,
            result.skipped,
            result.failed,
            result.rows,
        )

    result.success = result.failed == 0
    result.finished_at = time.time()
    _finalize_sync_job(
        _start_sync_job(warehouse, result, data_type="ADJUSTMENT_FACTOR"),
        warehouse, result,
        data_type="ADJUSTMENT_FACTOR",
        range_start=begin_date,
        range_end=end_date,
    )
    return result


# ============================================================
# Meta sync
# ============================================================

def sync_meta_codes(
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Refresh ``master.stock`` from the SDK."""
    settings = settings or get_worker_settings()
    warehouse = warehouse or get_warehouse(settings)
    result = SyncResult(job="sync_meta_codes", started_at=time.time())

    def _metadata_is_sparse(df: Optional[pd.DataFrame]) -> bool:
        """Return true when SDK code_info lacks stock_basic fields."""
        if df is None or (hasattr(df, "empty") and df.empty):
            return True
        sample = df.copy()
        rename_map = {
            "SECURITY_NAME": "name",
            "SECURITYNAME": "name",
            "SYMBOL": "name",
            "COMP_NAME": "comp_name",
            "LISTDATE": "list_date",
            "LISTPLATE_NAME": "list_plate",
        }
        sample = sample.rename(columns={k: v for k, v in rename_map.items() if k in sample.columns})
        if "name" not in sample.columns:
            return True
        name_blank = sample["name"].fillna("").astype(str).str.strip().eq("").mean()
        if name_blank > 0.9:
            return True
        for column in ("comp_name", "list_date", "list_plate"):
            if column not in sample.columns:
                return True
        return False

    try:
        adapter_obj = adapter or _get_adapter_safe()
        raw: Optional[pd.DataFrame] = None
        try:
            with _SDK_CALL_LOCK:
                raw = adapter_obj.get_code_info(security_type="EXTRA_STOCK_A")
                if _metadata_is_sparse(raw):
                    logger.info("SDK code_info is sparse; fetching stock_basic metadata")
                    raw = adapter_obj.get_stock_basic(summary_only=False)
        except Exception as e:
            logger.warning("SDK code info fetch failed: %s; using cached codes", e)
            cached_codes = _load_codes_from_meta(warehouse=warehouse, settings=settings)
            if cached_codes:
                raw = pd.DataFrame({"code": cached_codes})
            else:
                raise

        # SDK adapters may return code metadata in the index with only a
        # symbol/name column. Normalize that shape before merging cache data.
        code_aliases = {"MARKET_CODE", "SECURITY_CODE", "SECUCODE", "code"}
        if raw is not None and not raw.empty and not (code_aliases & set(raw.columns)):
            index_name = raw.index.name
            raw = raw.reset_index()
            raw = raw.rename(columns={raw.columns[0]: "code"})
            if index_name and index_name in raw.columns and index_name != "code":
                raw = raw.rename(columns={index_name: "code"})

        # Keep trusted display metadata from existing PostgreSQL rows when the SDK
        # response only contains the code list (or provides blank names).
        try:
            cached = warehouse.query_codes()
        except Exception as e:
            logger.warning("Failed to read cached code metadata: %s", e)
            cached = pd.DataFrame()
        if raw is not None and not raw.empty and not cached.empty and "code" in cached.columns:
            cached = cached.drop_duplicates("code").set_index("code")
            raw = raw.copy()
            if "code" in raw.columns:
                raw = raw.set_index("code")
                for column in (
                    "name",
                    "comp_name",
                    "pinyin",
                    "comp_name_eng",
                    "comp_sname_eng",
                    "industry",
                    "list_date",
                    "delist_date",
                    "list_plate",
                ):
                    if column not in raw.columns:
                        raw[column] = pd.NA
                    if column in cached.columns:
                        missing = raw[column].isna() | raw[column].astype(object).astype(str).eq("")
                        replacement = cached[column].reindex(raw.index)
                        values = raw[column].astype(object).tolist()
                        for position, is_missing in enumerate(missing.tolist()):
                            if is_missing:
                                values[position] = replacement.iloc[position]
                        raw[column] = values
                raw = raw.reset_index()

        if raw is not None and not (hasattr(raw, "empty") and raw.empty):
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
        written = warehouse.upsert_stocks(std)
        result.success = written > 0
        result.rows = len(std) if std is not None else 0
        result.finished_at = time.time()
    except Exception as e:
        logger.error("sync_meta_codes failed: %s", e)
        result.errors.append(str(e))
        result.finished_at = time.time()
    _finalize_sync_job(
        _start_sync_job(warehouse, result, data_type="SECURITY"),
        warehouse, result,
        data_type="SECURITY",
    )
    return result


def sync_meta_calendar(
    market: str = "SH",
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Refresh ``market.trade_calendar`` from the SDK."""
    settings = settings or get_worker_settings()
    warehouse = warehouse or get_warehouse(settings)
    result = SyncResult(job=f"sync_meta_calendar[{market}]", started_at=time.time())
    try:
        adapter_obj = adapter or _get_adapter_safe()
        with _SDK_CALL_LOCK:
            raw = adapter_obj.get_calendar(market=market)
        std = standardize_calendar_df(raw, market=market)
        written = warehouse.upsert_calendar(std)
        result.success = written > 0
        result.rows = len(std) if std is not None else 0
    except Exception as e:
        logger.error("sync_meta_calendar failed: %s", e)
        result.errors.append(str(e))
    result.finished_at = time.time()
    _finalize_sync_job(
        _start_sync_job(warehouse, result, data_type="TRADE_CALENDAR"),
        warehouse, result,
        data_type="TRADE_CALENDAR",
    )
    return result


# ============================================================
# Reference data sync
# ============================================================

def sync_financial(
    statement_type: str = "balance",
    *,
    codes: Optional[Sequence[str]] = None,
    batch_size: int = 50,
    offset: int = 0,
    merge: bool = True,
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Sync financial statement data to the warehouse reference table.

    NOTE: Currently disabled by scheduler (financial data not used; HDF5 cache
    consumes several GB). Kept here for manual ``backfill_financial`` runs.
    """
    settings = settings or get_worker_settings()
    warehouse = warehouse or get_warehouse(settings)
    del merge
    result = SyncResult(job=f"sync_financial_{statement_type}", started_at=time.time())

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
        combined.columns = [str(c).lower().strip() for c in combined.columns]
        if "ts_code" not in combined.columns and "code" in combined.columns:
            combined = combined.rename(columns={"code": "ts_code"})
        combined = _normalize_financial_df(combined, statement_type)
        written = warehouse.upsert_reference(statement_type, combined)
        result.success = written > 0
    else:
        result.success = result.failed == 0

    result.finished_at = time.time()
    logger.info(
        "sync_financial(%s) total=%d succeeded=%d skipped=%d failed=%d rows=%d duration=%.2fs",
        statement_type, result.total, result.succeeded, result.skipped,
        result.failed, result.rows, result.duration,
    )
    return result


def sync_shareholder(
    *,
    batch_size: int = 50,
    codes: Optional[Sequence[str]] = None,
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Sync shareholder-number data to the warehouse reference table."""
    settings = settings or get_worker_settings()
    warehouse = warehouse or get_warehouse(settings)
    result = SyncResult(job="sync_shareholder", started_at=time.time())

    if codes is None:
        codes = _load_codes_from_meta(warehouse=warehouse, settings=settings)
        if codes is None:
            try:
                adapter_obj = _get_adapter_safe()
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
    adapter_obj = adapter or _get_adapter_safe()

    for start in range(0, len(codes), batch_size):
        batch = codes[start : start + batch_size]
        batch_label = f"{start + 1}-{start + len(batch)}"
        try:
            with _SDK_CALL_LOCK:
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
        written = warehouse.upsert_reference("shareholder", combined)
        result.success = written > 0
    else:
        result.success = result.failed == 0

    result.finished_at = time.time()
    logger.info(
        "sync_shareholder total=%d succeeded=%d skipped=%d failed=%d rows=%d duration=%.2fs",
        result.total, result.succeeded, result.skipped, result.failed,
        result.rows, result.duration,
    )
    return result


def sync_index_component(
    *,
    index_codes: Optional[Sequence[str]] = None,
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Sync index constituent data to the warehouse reference table."""
    settings = settings or get_worker_settings()
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
        if "con_code" not in combined.columns and "code" in combined.columns:
            combined = combined.rename(columns={"code": "con_code"})
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
        written = warehouse.upsert_reference("index_member", combined)
        result.success = written > 0
    else:
        result.success = result.failed == 0

    result.finished_at = time.time()
    logger.info(
        "sync_index_component total=%d succeeded=%d skipped=%d failed=%d rows=%d duration=%.2fs",
        result.total, result.succeeded, result.skipped, result.failed,
        result.rows, result.duration,
    )
    return result


# ============================================================
# APScheduler glue
# ============================================================

_scheduler: Any = None
_scheduler_lock = threading.Lock()


def init_scheduler(settings: Optional[WorkerSettings] = None) -> Any:
    """Initialise the APScheduler instance if needed."""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            return _scheduler
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("APScheduler is not installed") from e

        settings = settings or get_worker_settings()
        scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        scheduler.add_listener(
            _on_job_event,
            EVENT_JOB_EXECUTED | EVENT_JOB_ERROR,
        )

        # Sync jobs — gated by sync_schedule_enabled.
        # NOTE: financial sync is disabled (HDF5 cache too large, data unused).
        if settings.sync_schedule_enabled:
            scheduler.add_job(
                _run_sync_kline_daily, "cron",
                hour=int(settings.sync_kline_daily_hour),
                minute=int(settings.sync_kline_daily_minute),
                id="sync_kline_daily", replace_existing=True,
            )
            scheduler.add_job(
                _run_sync_kline_weekly, "cron",
                day_of_week="fri",
                hour=int(settings.sync_kline_weekly_hour),
                minute=int(settings.sync_kline_weekly_minute),
                id="sync_kline_weekly", replace_existing=True,
            )
            scheduler.add_job(
                _run_sync_kline_monthly, "cron",
                day=1,
                hour=int(settings.sync_kline_monthly_hour),
                minute=int(settings.sync_kline_monthly_minute),
                id="sync_kline_monthly", replace_existing=True,
            )
            scheduler.add_job(
                _run_sync_meta_codes, "cron",
                hour=int(settings.sync_meta_codes_hour),
                minute=int(settings.sync_meta_codes_minute),
                id="sync_meta_codes", replace_existing=True,
            )
            calendar_minute = int(settings.sync_meta_codes_minute) + 10
            calendar_hour = (
                int(settings.sync_meta_codes_hour) + calendar_minute // 60
            ) % 24
            scheduler.add_job(
                _run_sync_meta_calendar, "cron",
                hour=calendar_hour,
                minute=calendar_minute % 60,
                id="sync_meta_calendar", replace_existing=True,
            )
            scheduler.add_job(
                _run_sync_shareholder, "cron",
                day_of_week=settings.sync_shareholder_day_of_week,
                hour=int(settings.sync_shareholder_hour),
                minute=int(settings.sync_shareholder_minute),
                id="sync_shareholder", replace_existing=True,
            )
            scheduler.add_job(
                _run_sync_index_component, "cron",
                day_of_week=settings.sync_index_component_day_of_week,
                hour=int(settings.sync_index_component_hour),
                minute=int(settings.sync_index_component_minute),
                id="sync_index_component", replace_existing=True,
            )
            # Keep a dedicated financial job slot for scheduler compatibility.
            # The worker itself remains a no-op while financial sync is disabled.
            scheduler.add_job(
                _run_sync_financial, "cron",
                day_of_week="sun",
                hour=5,
                minute=0,
                id="sync_financial", replace_existing=True,
            )

        if settings.maintenance_schedule_enabled:
            scheduler.add_job(
                _run_schema_verify, "cron",
                day_of_week=settings.maintenance_kline_day_of_week,
                hour=int(settings.maintenance_kline_hour),
                minute=int(settings.maintenance_kline_minute),
                id="schema_verify_kline_weekly", replace_existing=True,
            )
            scheduler.add_job(
                _run_schema_verify, "cron",
                day_of_week=settings.maintenance_financial_day_of_week,
                hour=int(settings.maintenance_financial_hour),
                minute=int(settings.maintenance_financial_minute),
                id="schema_verify_reference_weekly", replace_existing=True,
            )
            logger.info(
                "maintenance schedule enabled: kline=%s %02d:%02d, financial=%s %02d:%02d",
                settings.maintenance_kline_day_of_week,
                int(settings.maintenance_kline_hour),
                int(settings.maintenance_kline_minute),
                settings.maintenance_financial_day_of_week,
                int(settings.maintenance_financial_hour),
                int(settings.maintenance_financial_minute),
            )
        _scheduler = scheduler
        return scheduler


def start_scheduler() -> Any:
    scheduler = init_scheduler()
    with _scheduler_lock:
        if not scheduler.running:
            scheduler.start()
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None and _scheduler.running:
            try:
                _scheduler.shutdown(wait=False)
            except Exception:
                pass
        _scheduler = None


def get_scheduler() -> Any:
    return _scheduler


# ============================================================
# Scheduled job wrappers
# ============================================================

def _run_sync_kline_daily() -> None:
    try:
        settings = get_worker_settings()
        warehouse = get_warehouse(settings)
        end_date = int(datetime.now().strftime("%Y%m%d"))
        begin_date = 20200101
        try:
            last_date = warehouse.max_trade_date("day")
            if last_date:
                # 永久修复 (2026-07-31): begin_date = max_date + 1。
                # 旧逻辑 begin_date = max_date 时, 当 end_date 也是 max_date (同一天),
                # SDK 拿到 begin_date == end_date 的请求, 若 TGW 当天未发布,
                # 会回退返回 begin_date (即昨天) 的 DataFrame,
                # 被 ON CONFLICT DO UPDATE 误算成 succeeded (假阳性),
                # 今天的日志会显示 succeeded=5197 但 daily_bar.trade_date = today 行数 = 0。
                # 改成 max_date + 1 后, begin_date > max_date, SDK 端不会触发回退;
                # 若 TGW 当天确实没数据, 直接返回空 DataFrame, succeeded 会是 0,
                # sync_job 会暴露 job_status=failed/empty, 不再伪装成功。
                begin_date = int(
                    (datetime.strptime(str(last_date), "%Y%m%d").date() + timedelta(days=1)).strftime("%Y%m%d")
                )
                logger.info("Incremental daily sync from max_date+1: %s (max_date=%s)", begin_date, last_date)
        except Exception as e:
            logger.warning("Failed to probe last warehouse date, using default begin_date=20200101: %s", e)

        result = sync_kline_daily(from_date=begin_date, to_date=end_date)
        logger.info("sync_kline_daily: succeeded=%s failed=%s rows=%s duration=%.2fs",
                    result.succeeded, result.failed, result.rows, result.duration)
        factor_result = sync_adjustment_factors(
            from_date=_FACTOR_HISTORY_BEGIN_DATE,
            to_date=end_date,
            event_driven=True,
            event_lookback_sessions=5,
            settings=settings,
            warehouse=warehouse,
        )
        if not factor_result.success:
            logger.error(
                "sync_adjustment_factors(daily) failed: %s",
                factor_result.errors,
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_kline_daily failed: %s", e)


def _run_sync_kline_weekly() -> None:
    try:
        settings = get_worker_settings()
        warehouse = get_warehouse(settings)
        end_date = int(datetime.now().strftime("%Y%m%d"))
        begin_date = 20200101
        try:
            last_date = warehouse.max_trade_date("week")
            if last_date:
                # 永久修复 (2026-07-31): 同 daily 的 max_date + 1 修复,
                # 避免 begin_date == end_date (本周还没结算) 时 SDK 回退到上周, 假阳性。
                begin_date = int(
                    (datetime.strptime(str(last_date), "%Y%m%d").date() + timedelta(days=1)).strftime("%Y%m%d")
                )
                logger.info("Incremental weekly sync from max_date+1: %s (max_date=%s)", begin_date, last_date)
        except Exception:
            logger.warning("scheduled sync_kline_weekly: failed to probe last date, using 20200101")
        sync_kline_weekly(from_date=begin_date, to_date=end_date)
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_kline_weekly failed: %s", e)


def _run_sync_kline_monthly() -> None:
    try:
        settings = get_worker_settings()
        warehouse = get_warehouse(settings)
        end_date = int(datetime.now().strftime("%Y%m%d"))
        begin_date = 20200101
        try:
            last_date = warehouse.max_trade_date("month")
            if last_date:
                # 永久修复 (2026-07-31): 同 daily/weekly 的 max_date + 1 修复。
                begin_date = int(
                    (datetime.strptime(str(last_date), "%Y%m%d").date() + timedelta(days=1)).strftime("%Y%m%d")
                )
                logger.info("Incremental monthly sync from max_date+1: %s (max_date=%s)", begin_date, last_date)
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


def _run_sync_meta_calendar() -> None:
    try:
        sync_meta_calendar()
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_meta_calendar failed: %s", e)


def _run_schema_verify() -> None:
    """Scheduled PostgreSQL schema verification.

    The earlier "repair_kline" / "repair_financial" routines rewrote Parquet
    files, but the warehouse is now PostgreSQL-backed and the only safe
    "repair" available idempotently is :meth:`initialize_schema`, which
    covers every table — K-line, reference, financial. We keep two cron
    entries (kline-day, financial-day) so users can still stagger them via
    env, but both invoke this single function.
    """
    try:
        settings = get_worker_settings()
        warehouse = get_warehouse(settings)
        warehouse.initialize_schema()
        logger.info("scheduled PostgreSQL schema verification completed")
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled schema verification failed: %s", e)


def _run_sync_financial() -> None:
    """Run financial sync directly in scheduler thread with SDK lock.

    NOTE: Disabled in init_scheduler. Kept for manual one-off runs.
    """
    try:
        settings = get_worker_settings()
        warehouse = get_warehouse(settings)
        adapter = _get_adapter_safe()
        for statement_type in ("balance", "income", "cashflow"):
            logger.info("Starting financial sync: %s", statement_type)
            result = sync_financial(
                statement_type=statement_type,
                batch_size=50,
                settings=settings, warehouse=warehouse, adapter=adapter,
            )
            logger.info(
                "sync_financial(%s): success=%s rows=%s failed=%s duration=%.2fs",
                statement_type, result.success, result.rows, result.failed, result.duration,
            )
    except Exception as e:
        logger.exception("sync_financial failed: %s", e)


def _run_sync_shareholder() -> None:
    try:
        settings = get_worker_settings()
        warehouse = get_warehouse(settings)
        adapter = _get_adapter_safe()
        result = sync_shareholder(
            batch_size=50, settings=settings, warehouse=warehouse, adapter=adapter,
        )
        logger.info(
            "sync_shareholder: success=%s rows=%s failed=%s duration=%.2fs",
            result.success, result.rows, result.failed, result.duration,
        )
    except Exception as e:
        logger.exception("sync_shareholder failed: %s", e)


def _run_sync_index_component() -> None:
    try:
        settings = get_worker_settings()
        warehouse = get_warehouse(settings)
        adapter = _get_adapter_safe()
        result = sync_index_component(
            settings=settings, warehouse=warehouse, adapter=adapter,
        )
        logger.info(
            "sync_index_component: success=%s rows=%s failed=%s duration=%.2fs",
            result.success, result.rows, result.failed, result.duration,
        )
    except Exception as e:
        logger.exception("sync_index_component failed: %s", e)


# ============================================================
# Entry point
# ============================================================

def main() -> int:
    setup_logging()
    settings = get_worker_settings()
    _batch_start_time = time.time()
    heartbeat: Optional[HeartbeatWriter] = None

    logger.info("=" * 50)
    logger.info("AmazingData Batch starting...")
    logger.info("SDK: %s", settings.amazingdata_connection_string)
    logger.info("Redis: %s", settings.redis_url)
    logger.info("PostgreSQL: %s", settings.database_url.rsplit("@", 1)[-1])
    logger.info("=" * 50)

    if not _init_sdk_login():
        logger.error("Failed to login to AmazingData, exiting")
        return 1

    warehouse: Optional[HistoricalWarehouse] = None
    try:
        if settings.historical_enabled:
            warehouse = get_warehouse(settings)
            health = warehouse.health()
            logger.info(
                "PostgreSQL repository ready: connected=%s",
                health["database_connected"],
            )
        else:
            logger.info("Historical warehouse disabled")
    except Exception as e:
        logger.warning("Historical warehouse init failed: %s", e)

    def _signal_handler(signum, frame):  # noqa: ARG001
        logger.info("Received signal %s, shutting down...", signum)
        _shutdown_event.set()
        if heartbeat is not None:
            try:
                heartbeat.stop()
            except Exception:
                pass

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        start_scheduler()
        logger.info("Sync scheduler started")
    except Exception as e:
        logger.error("Sync scheduler init error: %s", e)
        return 1

    if warehouse is not None:
        for period in ("day", "week", "month"):
            _publish_period_freshness(period, warehouse, 0)
        logger.info("Historical freshness initialized from existing warehouse")

    scheduler_obj = get_scheduler()

    def _batch_heartbeat_payload() -> dict:
        now = time.time()
        with _last_job_lock:
            last = dict(_last_job)
        next_jobs = []
        state = "stopped"
        if scheduler_obj is not None and scheduler_obj.running:
            state = "running"
            for job in scheduler_obj.get_jobs():
                next_at = job.next_run_time
                next_jobs.append({
                    "id": job.id,
                    "next_run_at": (
                        next_at.isoformat() if next_at is not None else None
                    ),
                })
        return {
            "uptime_s": round(
                now - _batch_start_time, 3
            ),
            "last_job": last,
            "scheduler_state": state,
            "next_jobs": next_jobs,
        }

    def _batch_counter_snapshot() -> dict:
        return {}

    heartbeat = HeartbeatWriter(
        service_name="amazingdata-batch",
        get_payload=_batch_heartbeat_payload,
        get_counter_snapshot=_batch_counter_snapshot,
    )
    heartbeat.start()
    logger.info("Batch heartbeat writer started")

    logger.info("Batch worker running. Press Ctrl+C or send SIGTERM to stop.")
    try:
        while not _shutdown_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")

    logger.info("Shutting down batch worker...")
    shutdown_scheduler()

    try:
        get_adapter().logout()
        logger.info("AmazingData logged out")
    except Exception as e:
        logger.warning("Logout error: %s", e)

    logger.info("Batch stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
