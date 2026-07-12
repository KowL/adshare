"""DuckDB-backed query layer for the L3 historical data warehouse.

The warehouse creates an in-memory DuckDB connection and exposes
:class:`HistoricalWarehouse` for SQL-style queries against the on-disk
Parquet files. The connection is shared across threads with an internal
``threading.RLock`` to keep things safe in FastAPI's threadpool.

The on-disk layout is flat: one Parquet file per (period, code) with all
years merged (e.g. ``A_share/daily/000001.SZ.parquet``).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import duckdb
import pandas as pd

from adshare.core.config import Settings, get_settings
from adshare.core.logging import get_logger
from adshare.historical.models import _safe_code, normalize_period, period_to_subdir

logger = get_logger(__name__)


class HistoricalWarehouse:
    """DuckDB connection manager for the historical Parquet warehouse."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.root = Path(self.settings.historical_path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._con: Optional[duckdb.DuckDBPyConnection] = None
        self._lock = threading.RLock()
        self._max_rows = int(self.settings.duckdb_max_rows)
        self._query_timeout = int(self.settings.duckdb_query_timeout)
        self._init_directory_layout()
        self._init_connection()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _init_directory_layout(self) -> None:
        for sub in ("daily", "weekly", "monthly"):
            (self.root / "A_share" / sub).mkdir(parents=True, exist_ok=True)
        (self.root / "meta").mkdir(parents=True, exist_ok=True)
        (self.root / "snapshot").mkdir(parents=True, exist_ok=True)
        (self.root / "reference").mkdir(parents=True, exist_ok=True)

    def _init_connection(self) -> None:
        mode = (self.settings.duckdb_mode or "memory").lower()
        if mode == "file":
            db_path = Path(self.settings.duckdb_file_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._con = duckdb.connect(database=str(db_path))
        else:
            self._con = duckdb.connect(database=":memory:")

        # Use safe defaults; allow larger memory for analytical scans
        try:
            self._con.execute(f"PRAGMA threads={max(1, (os.cpu_count() or 2))}")
        except Exception:
            pass
        self._register_views()

    def _register_views(self) -> None:
        if self._con is None:
            return
        root = str(self.root)
        for period, view in (
            ("daily", "v_kline_day"),
            ("weekly", "v_kline_week"),
            ("monthly", "v_kline_month"),
        ):
            glob = f"{root}/A_share/{period}/*.parquet"
            sql = f"""
                CREATE OR REPLACE VIEW {view} AS
                SELECT
                    regexp_extract(filename, '.*[\\\\/]([^\\\\/]+)\\.parquet$', 1) AS code,
                    date, open, high, low, close, volume, amount,
                    adj_factor, is_suspended, sync_at
                FROM read_parquet('{glob}', filename=1, hive_partitioning=false)
            """
            try:
                self._con.execute(sql)
            except Exception as e:
                logger.warning("Failed to register view %s: %s", view, e)

        # Meta views
        try:
            self._con.execute(
                f"CREATE OR REPLACE VIEW v_calendar AS "
                f"SELECT * FROM read_parquet('{root}/meta/calendar.parquet')"
            )
        except Exception as e:
            logger.debug("v_calendar not registered: %s", e)

        try:
            self._con.execute(
                f"CREATE OR REPLACE VIEW v_codes AS "
                f"SELECT * FROM read_parquet('{root}/meta/codes.parquet')"
            )
        except Exception as e:
            logger.debug("v_codes not registered: %s", e)

        # Reference data views (financial, shareholder, index component, etc.)
        ref_root = f"{root}/reference"
        for view_name, file_name in (
            ("v_income", "income.parquet"),
            ("v_balance_sheet", "balance_sheet.parquet"),
            ("v_cashflow", "cashflow.parquet"),
            ("v_fina_indicator", "fina_indicator.parquet"),
            ("v_stk_holdernumber", "stk_holdernumber.parquet"),
            ("v_index_member", "index_member.parquet"),
            ("v_index_weight", "index_weight.parquet"),
            ("v_namechange", "namechange.parquet"),
        ):
            path = f"{ref_root}/{file_name}"
            try:
                self._con.execute(
                    f"CREATE OR REPLACE VIEW {view_name} AS "
                    f"SELECT * FROM read_parquet('{path}')"
                )
            except Exception as e:
                logger.debug("%s not registered: %s", view_name, e)

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                try:
                    self._con.close()
                except Exception:
                    pass
                self._con = None

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            with self._lock:
                if self._con is None:
                    self._init_connection()
        assert self._con is not None
        return self._con

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def kline_dir(self, period: str, year: Optional[int] = None) -> Path:
        """Return the K-line directory for a period.

        ``year`` is accepted for backward compatibility but is ignored in
        the flat layout — every code lives directly under
        ``A_share/{subdir}/``.
        """
        del year
        subdir = normalize_period(period)
        return self.root / "A_share" / subdir

    def meta_dir(self) -> Path:
        return self.root / "meta"

    # ------------------------------------------------------------------
    # Sync status
    # ------------------------------------------------------------------

    def is_synced(
        self,
        begin_date: int,
        end_date: int,
        period: str,
        codes: Optional[Sequence[str]] = None,
    ) -> bool:
        """Return True if the requested range is fully covered locally.

        A range is considered ``synced`` if:

        1. The ``A_share/{subdir}/`` directory exists and contains at least
           one Parquet file.
        2. Every requested code has a file (only checked when ``codes`` is
           non-empty).
        3. The on-disk file for each requested code covers the
           ``[begin_date, end_date]`` window (only when ``codes`` is given
           and ``period == "day"``). The check verifies
           ``min(date) <= begin_date`` and ``max(date) >= end_date``
           against the actual file contents.
        """
        subdir = normalize_period(period)
        period_dir = self.root / "A_share" / subdir
        if not period_dir.exists():
            return False
        parquet_files = list(period_dir.glob("*.parquet"))
        if not parquet_files:
            return False
        if codes:
            expected = {_safe_code(c) for c in codes}
            found = {f.stem for f in parquet_files}
            if not expected.issubset(found):
                return False
        if codes and subdir == "daily":
            try:
                local = self.query_kline(codes, begin_date, end_date, period)
            except Exception as e:  # noqa: BLE001
                logger.debug("is_synced coverage check failed: %s", e)
                return False
            if local.empty or "code" not in local.columns or "date" not in local.columns:
                return False
            dates = pd.to_numeric(local["date"], errors="coerce").fillna(0).astype(int)
            local = local.assign(date=dates)
            for code in codes:
                code_df = local[local["code"].astype(str) == str(code)]
                if code_df.empty:
                    return False
                if int(begin_date) == int(end_date):
                    if not (code_df["date"] == int(begin_date)).any():
                        return False
                    continue
                if int(code_df["date"].min()) > int(begin_date):
                    return False
                if int(code_df["date"].max()) < int(end_date):
                    return False
        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def query_kline(
        self,
        codes: Sequence[str],
        begin_date: int,
        end_date: int,
        period: str = "day",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> pd.DataFrame:
        """Query K-line rows from the on-disk Parquet files."""
        if not codes:
            return pd.DataFrame()

        subdir = normalize_period(period)
        safe_codes = [_safe_code(c) for c in codes]

        begin = int(begin_date)
        end = int(end_date)

        # Build explicit file list instead of glob — globbing 10k+ parquet files
        # and then regexp-extracting the code out of each filename took ~45s for
        # full-market queries. Listing the 5000+ files we actually need drops it
        # to ~5s (8x faster, measured 2026-06-11).
        #
        # Critical: do NOT add a `WHERE code IN (?,?,?)` filter on top of the
        # explicit file list. read_parquet(['a','b',...]) already restricts to
        # those files, and the IN clause forces DuckDB's planner to build a
        # 10k-element hash table on a cold connection — measured at 40s vs
        # 0.6s without IN (2026-06-11). The code column is derived from the
        # filename so we can still return it for downstream filters.
        file_paths = [
            str(Path(self.root) / "A_share" / subdir / f"{code}.parquet")
            for code in safe_codes
        ]
        # Drop any codes without a parquet file (avoid read_parquet errors on
        # missing paths).
        existing_paths: list[str] = []
        for path in file_paths:
            if Path(path).exists():
                existing_paths.append(path)
        if not existing_paths:
            return pd.DataFrame()
        file_list_sql = "[" + ",".join(f"'{p}'" for p in existing_paths) + "]"

        params: List[Any] = [begin, end]

        sql = f"""
            SELECT
                regexp_extract(filename, '.*/([^/]+)\\.parquet$', 1) AS code,
                date, open, high, low, close, volume, amount,
                adj_factor, is_suspended, sync_at
            FROM read_parquet({file_list_sql}, filename=true, union_by_name=true)
            WHERE date BETWEEN ? AND ?
            ORDER BY code, date
        """

        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])

        return self._execute_df(sql, params)

    def query_calendar(
        self,
        market: Optional[str] = None,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        path = self.meta_dir() / "calendar.parquet"
        if not path.exists():
            return pd.DataFrame(columns=[
                "date", "market", "is_trading_day", "weekday", "sync_at"
            ])
        sql = f"SELECT * FROM read_parquet('{path}') WHERE 1=1"
        params: List[Any] = []
        if market:
            sql += " AND market = ?"
            params.append(market)
        if begin_date is not None:
            sql += " AND date >= ?"
            params.append(int(begin_date))
        if end_date is not None:
            sql += " AND date <= ?"
            params.append(int(end_date))
        sql += " ORDER BY date"
        return self._execute_df(sql, params)

    def query_codes(
        self,
        board: Optional[str] = None,
        is_listed: Optional[bool] = None,
    ) -> pd.DataFrame:
        path = self.meta_dir() / "codes.parquet"
        if not path.exists():
            return pd.DataFrame(columns=[
                "code", "name", "list_date", "delist_date",
                "is_listed", "board", "industry", "sync_at",
            ])
        sql = f"SELECT * FROM read_parquet('{path}') WHERE 1=1"
        params: List[Any] = []
        if board:
            sql += " AND board = ?"
            params.append(board)
        if is_listed is not None:
            sql += " AND is_listed = ?"
            params.append(bool(is_listed))
        sql += " ORDER BY code"
        return self._execute_df(sql, params)

    def _view_exists(self, view_name: str) -> bool:
        """Check if a DuckDB view exists."""
        try:
            rows = self.connection.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                [view_name],
            ).fetchall()
            return bool(rows and rows[0][0] > 0)
        except Exception:
            return False

    def _query_reference(
        self,
        view_name: str,
        file_name: str,
        ts_code_col: str = "ts_code",
        ts_code: Optional[str] = None,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
        date_col: Optional[str] = None,
    ) -> pd.DataFrame:
        """Generic query helper for reference Parquet files."""
        path = self.root / "reference" / file_name
        if not path.exists():
            return pd.DataFrame()

        source = view_name if self._view_exists(view_name) else f"read_parquet('{path}')"
        sql = f"SELECT * FROM {source} WHERE 1=1"
        params: List[Any] = []

        if ts_code:
            sql += f" AND {ts_code_col} = ?"
            params.append(ts_code)
        if date_col and begin_date is not None:
            sql += f" AND {date_col} >= ?"
            params.append(int(begin_date))
        if date_col and end_date is not None:
            sql += f" AND {date_col} <= ?"
            params.append(int(end_date))
        return self._execute_df(sql, params)

    def query_financial(
        self,
        statement_type: str,
        ts_code: Optional[str] = None,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        """Query financial statement data from reference table."""
        view_map = {
            "income": ("v_income", "income.parquet"),
            "balance": ("v_balance_sheet", "balance_sheet.parquet"),
            "balance_sheet": ("v_balance_sheet", "balance_sheet.parquet"),
            "cashflow": ("v_cashflow", "cashflow.parquet"),
        }
        view_name, file_name = view_map.get(statement_type, (None, None))
        if view_name is None:
            return pd.DataFrame()
        return self._query_reference(
            view_name,
            file_name,
            ts_code_col="market_code",
            ts_code=ts_code,
            begin_date=begin_date,
            end_date=end_date,
            date_col="reporting_period",
        )

    def query_shareholder(
        self,
        ts_code: Optional[str] = None,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        """Query shareholder number data from reference table."""
        return self._query_reference(
            "v_stk_holdernumber",
            "stk_holdernumber.parquet",
            ts_code=ts_code,
            begin_date=begin_date,
            end_date=end_date,
            date_col="end_date",
        )

    def query_index_member(
        self,
        index_code: Optional[str] = None,
        ts_code: Optional[str] = None,
    ) -> pd.DataFrame:
        """Query index constituent data from reference table."""
        path = self.root / "reference" / "index_member.parquet"
        if not path.exists():
            return pd.DataFrame()
        source = "v_index_member" if self._view_exists("v_index_member") else f"read_parquet('{path}')"
        sql = f"SELECT * FROM {source} WHERE 1=1"
        params: List[Any] = []
        if index_code:
            sql += " AND index_code = ?"
            params.append(index_code)
        if ts_code:
            sql += " AND con_code = ?"
            params.append(ts_code)
        return self._execute_df(sql, params)

    # ------------------------------------------------------------------
    # SQL interface (constrained)
    # ------------------------------------------------------------------

    def execute_sql(self, sql: str, max_rows: Optional[int] = None) -> pd.DataFrame:
        """Run a constrained read-only SQL query.

        Only ``SELECT`` and CTE-style ``WITH`` statements are allowed.
        ``ATTACH``, ``COPY`` and ``LOAD`` are rejected. Result size is
        limited to ``settings.duckdb_max_rows`` unless a lower caller cap is
        supplied. One extra row may be returned so callers can detect
        truncation exactly.
        """
        if not sql or not sql.strip():
            raise ValueError("empty SQL statement")
        cleaned = sql.strip().lstrip("(").strip()
        head = cleaned.split(None, 1)[0].upper() if cleaned else ""
        if head not in {"SELECT", "WITH"}:
            raise ValueError("only SELECT/CTE statements are allowed")
        forbidden = ("ATTACH", "COPY", "LOAD", "INSTALL", "EXPORT", "SET")
        upper_sql = " ".join(cleaned.split())
        for tok in forbidden:
            if tok + " " in upper_sql.upper() or upper_sql.upper().endswith(tok):
                raise ValueError(f"statement '{tok}' is not allowed")

        row_cap = min(int(max_rows or self._max_rows), self._max_rows)
        row_cap = max(1, row_cap)
        wrapped = f"SELECT * FROM ({sql}) LIMIT {row_cap + 1}"
        with self._lock:
            try:
                df = self._con.execute(wrapped).fetch_df()
            except Exception as e:
                raise RuntimeError(f"SQL execution failed: {e}") from e
        return df

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def refresh_views(self) -> None:
        """Re-register DuckDB views (call after writing new files)."""
        with self._lock:
            self._register_views()

    def list_files(
        self,
        period: Optional[str] = None,
        year: Optional[int] = None,
    ) -> List[Path]:
        root = self.root
        results: List[Path] = []
        del year  # flat layout: every code lives directly under A_share/{subdir}/
        if period:
            subdir = normalize_period(period)
            base = root / "A_share" / subdir
            if base.exists():
                results.extend(sorted(p for p in base.glob("*.parquet")))
            return results
        for sub in ("daily", "weekly", "monthly"):
            base = root / "A_share" / sub
            if not base.exists():
                continue
            results.extend(sorted(p for p in base.glob("*.parquet")))
        return results

    def stats(self) -> Dict[str, Any]:
        """Return aggregate statistics of the warehouse."""
        period_stats: Dict[str, Any] = {}
        for sub in ("daily", "weekly", "monthly"):
            base = self.root / "A_share" / sub
            file_count = 0
            total_bytes = 0
            if base.exists():
                for f in base.glob("*.parquet"):
                    file_count += 1
                    total_bytes += f.stat().st_size
            # Pull min/max date from a single DuckDB scan over the view.
            first_date: Optional[int] = None
            last_date: Optional[int] = None
            if file_count > 0:
                try:
                    row = self.connection.execute(
                        f"SELECT MIN(date) AS lo, MAX(date) AS hi FROM v_kline_{'day' if sub == 'daily' else ('week' if sub == 'weekly' else 'month')}"
                    ).fetchone()
                    if row:
                        first_date, last_date = row[0], row[1]
                except Exception as e:  # noqa: BLE001
                    logger.debug("stats date range probe failed for %s: %s", sub, e)
            period_stats[sub] = {
                "file_count": file_count,
                "total_bytes": total_bytes,
                "first_date": int(first_date) if first_date is not None else None,
                "last_date": int(last_date) if last_date is not None else None,
            }
        return {
            "root": str(self.root),
            "duckdb_mode": self.settings.duckdb_mode,
            "periods": period_stats,
        }

    def health(self) -> Dict[str, Any]:
        try:
            with self._lock:
                self._con.execute("SELECT 1").fetchone()
            duckdb_ok = True
        except Exception as e:
            duckdb_ok = False
            logger.debug("duckdb health probe failed: %s", e)
        return {
            "historical_enabled": self.settings.historical_enabled,
            "root": str(self.root),
            "duckdb_connected": duckdb_ok,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _execute_df(self, sql: str, params: Optional[Sequence[Any]] = None) -> pd.DataFrame:
        params = list(params or [])
        with self._lock:
            try:
                if params:
                    cur = self._con.execute(sql, params)
                else:
                    cur = self._con.execute(sql)
                return cur.fetch_df()
            except Exception as e:
                msg = str(e)
                if (
                    "No files found" in msg
                    or "IO Error" in msg
                    or "No such file" in msg
                    or "No match found" in msg
                ):
                    return pd.DataFrame()
                raise


# Note: ``_safe_code`` is imported from :mod:`adshare.historical.models`.


# ----------------------------------------------------------------------
# Singleton accessor
# ----------------------------------------------------------------------

_warehouse: Optional[HistoricalWarehouse] = None
_warehouse_lock = threading.Lock()


def get_warehouse(settings: Optional[Settings] = None) -> HistoricalWarehouse:
    """Return the process-wide :class:`HistoricalWarehouse` singleton."""
    global _warehouse
    if _warehouse is None:
        with _warehouse_lock:
            if _warehouse is None:
                _warehouse = HistoricalWarehouse(settings=settings)
    return _warehouse


def reset_warehouse() -> None:
    """Tear down the singleton (used by tests)."""
    global _warehouse
    with _warehouse_lock:
        if _warehouse is not None:
            try:
                _warehouse.close()
            except Exception:
                pass
        _warehouse = None
