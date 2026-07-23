"""盘后模式: APScheduler 定时同步 -> L3 warehouse (Parquet/DuckDB).

启动:
    python -m amazingdata.batch

镜像:
    amazingdata-batch  (FROM amazingdata-base)

Docker:
    docker compose -f amazingdata/docker-compose.batch.yml up -d

职责:
- 登录 AmazingData SDK
- 初始化 HistoricalWarehouse (DuckDB views + Parquet layout)
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
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

# Allow running as ``python amazingdata/batch.py``
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from amazingdata.config import WorkerSettings, get_worker_settings  # noqa: E402
from adshare.core.config import Settings as SharedSettings  # noqa: E402  # noqa: E402
from adshare.core.logging import setup_logging, get_logger  # noqa: E402
from adshare.historical.models import (  # noqa: E402
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
from adshare.historical.warehouse import HistoricalWarehouse, get_warehouse  # noqa: E402

from amazingdata.adapters.amazingdata import get_adapter  # noqa: E402
from amazingdata.adapters.base import DataSourceAdapter  # noqa: E402

logger = get_logger("amazingdata.batch")

_shutdown_event = threading.Event()


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
    """Load the A-share code list from the cached ``meta/codes.parquet``.

    Returns ``None`` if the file is missing or empty. Reference sync jobs
    use this cache as their primary code source so they do not depend on
    the SDK's ``BaseData.get_code_list`` / ``get_code_info`` calls, which
    have been observed returning ``None`` or raising ``'NoneType' object
    is not subscriptable`` when the SDK session is under pressure.
    """
    try:
        settings = settings or get_worker_settings()
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
    """Standardize, validate and merge with any existing Parquet file."""
    if df is None or df.empty:
        return None
    std = standardize_kline_df(df, code=code)
    std = validate_kline_df(std)
    if std.empty:
        return None
    code_key = _ensure_code_suffix(code)
    file_path = kline_file_path(root, period, code_key)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    if file_path.exists():
        try:
            existing = pd.read_parquet(file_path)
            if not existing.empty:
                std = pd.concat([existing, std], ignore_index=True)
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
            pass

    std.to_parquet(file_path, engine="pyarrow", compression="zstd", index=False)
    return file_path


def _persist_meta(df: pd.DataFrame, path: Path) -> Optional[Path]:
    if df is None or df.empty:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
    return path


def _persist_reference(df: pd.DataFrame, path: Path) -> Optional[Path]:
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
                period, begin_date, end_date,
                min(start + len(batch), len(codes)), len(codes),
                result.succeeded, result.skipped, result.failed, rows_written,
            )

        result.rows = rows_written
        result.finished_at = time.time()
        result.success = result.failed == 0
        _write_period_metadata(period, root, warehouse, rows_written)
        logger.info(
            "sync_kline(%s) range=[%s,%s] succeeded=%d skipped=%d failed=%d rows=%d duration=%.2fs",
            period, begin_date, end_date,
            result.succeeded, result.skipped, result.failed, result.rows, result.duration,
        )
        return result

    def _sync_one(code: str) -> tuple[str, str, Optional[Path], Optional[str]]:
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
        period, begin_date, end_date,
        result.succeeded, result.failed, result.rows, result.duration,
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
        warehouse.refresh_views()
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
            root, period,
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


def sync_adjustment_factors(
    *,
    from_date: Optional[int] = None,
    to_date: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    periods: Sequence[str] = ("daily", "weekly", "monthly"),
    refresh: bool = True,
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Fetch real cumulative factors and patch existing K-line files."""
    settings = settings or get_worker_settings()
    warehouse = warehouse or get_warehouse(settings)
    adapter_obj = adapter or _get_adapter_safe()
    begin_date, end_date = _date_bounds(from_date, to_date)

    if codes is None:
        codes = _load_codes_from_meta(warehouse=warehouse, settings=settings)
    code_list = [_ensure_code_suffix(code) for code in (codes or [])]
    result = SyncResult(
        job="sync_adjustment_factors",
        started_at=time.time(),
        total=len(code_list) * len(periods),
    )
    if not code_list:
        result.success = True
        result.finished_at = time.time()
        return result

    local_path = Path(settings.amazingdata_local_path)
    local_path.mkdir(parents=True, exist_ok=True)
    try:
        with _SDK_CALL_LOCK:
            factors = adapter_obj.get_adjustment_factors(
                codes=",".join(code_list),
                begin_date=begin_date,
                end_date=end_date,
                local_path=str(local_path),
                refresh=refresh,
            )
    except Exception as exc:  # noqa: BLE001
        result.failed = result.total
        result.errors.append(str(exc))
        result.finished_at = time.time()
        return result

    required = {"code", "date", "adj_factor"}
    if not isinstance(factors, pd.DataFrame) or not required <= set(factors.columns):
        result.failed = result.total
        result.errors.append(
            "adjustment factor source returned no canonical factor data"
        )
        result.finished_at = time.time()
        return result

    factors = factors.copy()
    factors["code"] = factors["code"].astype(str)
    factors["date"] = pd.to_numeric(factors["date"], errors="coerce")
    factors["adj_factor"] = pd.to_numeric(
        factors["adj_factor"], errors="coerce"
    )
    factors = factors.dropna(subset=["date", "adj_factor"])
    factors["date"] = factors["date"].astype(int)

    for period in periods:
        subdir = normalize_period(period)
        for code in code_list:
            path = kline_file_path(warehouse.root, subdir, code)
            if not path.exists():
                result.skipped += 1
                continue
            try:
                frame = pd.read_parquet(path)
                code_factors = (
                    factors[factors["code"] == code][["date", "adj_factor"]]
                    .drop_duplicates("date", keep="last")
                    .sort_values("date")
                )
                if frame.empty or code_factors.empty:
                    result.skipped += 1
                    continue

                factor_series = code_factors.set_index("date")["adj_factor"]
                target_dates = pd.Index(
                    pd.to_numeric(frame["date"], errors="coerce")
                    .dropna()
                    .astype(int)
                    .unique()
                )
                replacement = (
                    factor_series.reindex(
                        factor_series.index.union(target_dates)
                    )
                    .sort_index()
                    .ffill()
                    .reindex(target_dates)
                )
                factor_by_date = replacement.to_dict()
                factor_values = pd.to_numeric(
                    frame["date"], errors="coerce"
                ).map(factor_by_date)
                mask = factor_values.notna()
                if not mask.any():
                    result.skipped += 1
                    continue

                frame = frame.copy()
                frame.loc[mask, "adj_factor"] = factor_values[mask].astype(float)
                frame.to_parquet(
                    path,
                    engine="pyarrow",
                    compression="zstd",
                    index=False,
                )
                result.succeeded += 1
                result.rows += int(mask.sum())
            except Exception as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append(f"{subdir}/{code}: {exc}")

    warehouse.refresh_views()
    result.success = result.failed == 0
    result.finished_at = time.time()
    return result


# ============================================================
# Meta sync
# ============================================================

def sync_meta_codes(
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Refresh ``meta/codes.parquet`` from the SDK."""
    settings = settings or get_worker_settings()
    warehouse = warehouse or get_warehouse(settings)
    result = SyncResult(job="sync_meta_codes", started_at=time.time())

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

        # SDK adapters may return code metadata in the index with only a
        # symbol/name column. Normalize that shape before merging cache data.
        if raw is not None and not raw.empty and "code" not in raw.columns:
            index_name = raw.index.name
            raw = raw.reset_index()
            raw = raw.rename(columns={raw.columns[0]: "code"})
            if index_name and index_name in raw.columns and index_name != "code":
                raw = raw.rename(columns={index_name: "code"})

        # Keep trusted display metadata from an existing cache when the SDK
        # response only contains the code list (or provides blank names).
        cached_path = warehouse.meta_dir() / "codes.parquet"
        cached = pd.DataFrame()
        if cached_path.exists():
            try:
                cached = pd.read_parquet(cached_path)
            except Exception as e:
                logger.warning("Failed to read cached code metadata: %s", e)
        if raw is not None and not raw.empty and not cached.empty and "code" in cached.columns:
            cached = cached.drop_duplicates("code").set_index("code")
            raw = raw.copy()
            if "code" in raw.columns:
                raw = raw.set_index("code")
                for column in ("name", "comp_name", "industry", "list_date", "delist_date"):
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
        path = _persist_meta(std, warehouse.meta_dir() / "codes.parquet")
        result.success = path is not None
        result.rows = len(std) if std is not None else 0
        result.finished_at = time.time()
    except Exception as e:
        logger.error("sync_meta_codes failed: %s", e)
        result.errors.append(str(e))
        result.finished_at = time.time()
    warehouse.refresh_views()
    return result


def sync_meta_calendar(
    market: str = "SH",
    settings: Optional[WorkerSettings] = None,
    warehouse: Optional[HistoricalWarehouse] = None,
    adapter=None,
) -> SyncResult:
    """Refresh ``meta/calendar.parquet`` from the SDK."""
    settings = settings or get_worker_settings()
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
        target_path = warehouse.root / "reference" / file_name
        if merge and target_path.exists():
            try:
                existing = pd.read_parquet(target_path)
                existing = _normalize_financial_df(existing, statement_type)
                combined = pd.concat([existing, combined], ignore_index=True)
                dup_cols = _financial_dedup_keys(statement_type)
                combined = combined.drop_duplicates(subset=dup_cols, keep="last")
            except Exception:
                pass
        path = _persist_reference(combined, target_path)
        result.success = path is not None
        warehouse.refresh_views()
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
        path = _persist_reference(combined, warehouse.root / "reference" / "stk_holdernumber.parquet")
        result.success = path is not None
        warehouse.refresh_views()
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
        path = _persist_reference(combined, warehouse.root / "reference" / "index_member.parquet")
        result.success = path is not None
        warehouse.refresh_views()
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

        settings = settings or get_worker_settings()
        scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

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
                _run_repair_kline, "cron",
                day_of_week=settings.maintenance_kline_day_of_week,
                hour=int(settings.maintenance_kline_hour),
                minute=int(settings.maintenance_kline_minute),
                id="repair_kline_weekly", replace_existing=True,
            )
            scheduler.add_job(
                _run_repair_financial, "cron",
                day_of_week=settings.maintenance_financial_day_of_week,
                hour=int(settings.maintenance_financial_hour),
                minute=int(settings.maintenance_financial_minute),
                id="repair_financial_weekly", replace_existing=True,
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


def start_scheduler() -> Optional["BackgroundScheduler"]:  # type: ignore[name-defined]
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


def get_scheduler() -> Optional["BackgroundScheduler"]:  # type: ignore[name-defined]
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
            warehouse.refresh_views()
            row = warehouse.connection.execute("SELECT MAX(date) FROM v_kline_day").fetchone()
            last_date = row[0] if row and row[0] else None
            if last_date:
                begin_date = int(last_date)
                logger.info("Incremental daily sync from last warehouse date: %s", begin_date)
        except Exception as e:
            logger.warning("Failed to probe last warehouse date, using default begin_date=20200101: %s", e)

        result = sync_kline_daily(from_date=begin_date, to_date=end_date)
        logger.info("sync_kline_daily: succeeded=%s failed=%s rows=%s duration=%.2fs",
                    result.succeeded, result.failed, result.rows, result.duration)
        factor_result = sync_adjustment_factors(
            from_date=_DEFAULT_BEGIN_DATE,
            to_date=end_date,
            periods=("daily",),
            settings=settings,
            warehouse=warehouse,
        )
        if not factor_result.success:
            logger.error(
                "sync_adjustment_factors(daily) failed: %s",
                factor_result.errors,
            )
        _sync_to_remote()
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_kline_daily failed: %s", e)


def _run_sync_kline_weekly() -> None:
    try:
        settings = get_worker_settings()
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
        sync_adjustment_factors(
            from_date=_DEFAULT_BEGIN_DATE,
            to_date=end_date,
            periods=("weekly",),
            refresh=False,
            settings=settings,
            warehouse=warehouse,
        )
        _sync_to_remote()
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_kline_weekly failed: %s", e)


def _run_sync_kline_monthly() -> None:
    try:
        settings = get_worker_settings()
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
        sync_adjustment_factors(
            from_date=_DEFAULT_BEGIN_DATE,
            to_date=end_date,
            periods=("monthly",),
            refresh=False,
            settings=settings,
            warehouse=warehouse,
        )
        _sync_to_remote()
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled sync_kline_monthly failed: %s", e)


def _sync_to_remote() -> None:
    """Push updated L3 data to the remote adshare-api server."""
    project_root = Path(__file__).resolve().parents[1]
    script = project_root / "scripts" / "sync_to_remote.sh"
    if not script.exists():
        logger.warning("sync_to_remote: script not found at %s", script)
        return
    logger.info("sync_to_remote: starting %s", script)
    try:
        result = subprocess.run(
            ["/bin/bash", str(script)],
            capture_output=True, text=True, check=False, timeout=1800,
        )
        if result.returncode == 0:
            logger.info("sync_to_remote: success\n%s", result.stdout)
        else:
            logger.error(
                "sync_to_remote: failed (exit %s)\nstdout:\n%s\nstderr:\n%s",
                result.returncode, result.stdout, result.stderr,
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


def _run_repair_kline() -> None:
    try:
        from adshare.historical.maintenance import (
            repair_kline_directory, repair_codes_table,
        )
        settings = get_worker_settings()
        warehouse = get_warehouse(settings)
        r1 = repair_kline_directory(dry_run=False, warehouse=warehouse)
        r2 = repair_codes_table(dry_run=False, warehouse=warehouse)
        logger.info("scheduled maintenance: kline %s | codes %s", r1.summary(), r2.summary())
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled repair_kline failed: %s", e)


def _run_repair_financial() -> None:
    try:
        from adshare.historical.maintenance import repair_financial_table
        settings = get_worker_settings()
        warehouse = get_warehouse(settings)
        r = repair_financial_table(dry_run=False, warehouse=warehouse)
        logger.info("scheduled maintenance: financial %s", r.summary())
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled repair_financial failed: %s", e)


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

    logger.info("=" * 50)
    logger.info("AmazingData Batch starting...")
    logger.info("SDK: %s", settings.amazingdata_connection_string)
    logger.info("Redis: %s", settings.redis_url)
    logger.info("Warehouse: %s", settings.historical_path)
    logger.info("=" * 50)

    if not _init_sdk_login():
        logger.error("Failed to login to AmazingData, exiting")
        return 1

    try:
        if settings.historical_enabled:
            warehouse = get_warehouse(settings)
            health = warehouse.health()
            logger.info("Historical warehouse ready: root=%s duckdb=%s",
                        health["root"], health["duckdb_connected"])
        else:
            logger.info("Historical warehouse disabled")
    except Exception as e:
        logger.warning("Historical warehouse init failed: %s", e)

    def _signal_handler(signum, frame):  # noqa: ARG001
        logger.info("Received signal %s, shutting down...", signum)
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        start_scheduler()
        logger.info("Sync scheduler started")
    except Exception as e:
        logger.error("Sync scheduler init error: %s", e)
        return 1

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
