"""DuckDB-backed query layer for the L3 historical data warehouse.

The warehouse creates an in-memory DuckDB connection and exposes
:class:`HistoricalWarehouse` for SQL-style queries against the on-disk
Parquet files. The connection is shared across threads with an internal
``threading.RLock`` to keep things safe in FastAPI's threadpool.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import duckdb
import pandas as pd

from adshare.core.config import Settings, get_settings
from adshare.core.logging import get_logger
from adshare.historical.models import normalize_period, period_to_subdir

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
            for year in range(1990, datetime.now().year + 2):
                (self.root / "A_share" / sub / str(year)).mkdir(parents=True, exist_ok=True)
        (self.root / "meta").mkdir(parents=True, exist_ok=True)
        (self.root / "snapshot").mkdir(parents=True, exist_ok=True)

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
            glob = f"{root}/A_share/{period}/*/*.parquet"
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

    def kline_dir(self, period: str, year: int) -> Path:
        subdir = normalize_period(period)
        return self.root / "A_share" / subdir / str(int(year))

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

        A range is considered ``synced`` if, for every year it touches,
        the file for every requested code exists on disk. A empty
        ``codes`` list means we only check that at least one file exists
        for the period/year combination.
        """
        subdir = normalize_period(period)
        try:
            begin_year = int(str(int(begin_date))[:4])
            end_year = int(str(int(end_date))[:4])
        except Exception:
            return False
        for year in range(begin_year, end_year + 1):
            year_dir = self.root / "A_share" / subdir / str(year)
            if not year_dir.exists():
                return False
            parquet_files = list(year_dir.glob("*.parquet"))
            if not parquet_files:
                return False
            if codes:
                expected = {_safe_code(c) for c in codes}
                found = {f.stem for f in parquet_files}
                if not expected.issubset(found):
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
        begin_year = int(str(begin)[:4])
        end_year = int(str(end)[:4])

        # Build a single glob covering only the relevant years. DuckDB's
        # read_parquet does not support bash brace expansion, so we use a
        # wildcard and filter the code in the WHERE clause.
        if begin_year == end_year:
            glob = f"{self.root}/A_share/{subdir}/{int(begin_year)}/*.parquet"
        else:
            glob = f"{self.root}/A_share/{subdir}/*/*.parquet"

        placeholders = ",".join("?" for _ in safe_codes)
        params: List[Any] = list(safe_codes) + [begin, end]

        sql = f"""
            SELECT
                regexp_extract(filename, '.*[\\\\/]([^\\\\/]+)\\.parquet$', 1) AS code,
                date, open, high, low, close, volume, amount,
                adj_factor, is_suspended, sync_at
            FROM read_parquet('{glob}', filename=1, hive_partitioning=false)
            WHERE regexp_extract(filename, '.*[\\\\/]([^\\\\/]+)\\.parquet$', 1) IN ({placeholders})
              AND date BETWEEN ? AND ?
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
        if period:
            subdir = normalize_period(period)
            base = root / "A_share" / subdir
            if year is not None:
                base = base / str(int(year))
            if base.exists():
                results.extend(sorted(p for p in base.glob("*.parquet")))
            return results
        for sub in ("daily", "weekly", "monthly"):
            base = root / "A_share" / sub
            if not base.exists():
                continue
            if year is not None:
                base = base / str(int(year))
                if base.exists():
                    results.extend(sorted(p for p in base.glob("*.parquet")))
            else:
                results.extend(sorted(p for p in base.rglob("*.parquet")))
        return results

    def stats(self) -> Dict[str, Any]:
        """Return aggregate statistics of the warehouse."""
        period_stats: Dict[str, Any] = {}
        for sub in ("daily", "weekly", "monthly"):
            base = self.root / "A_share" / sub
            file_count = 0
            total_bytes = 0
            year_dirs: List[str] = []
            if base.exists():
                for year_dir in sorted(base.iterdir()):
                    if not year_dir.is_dir():
                        continue
                    year_dirs.append(year_dir.name)
                    for f in year_dir.glob("*.parquet"):
                        file_count += 1
                        total_bytes += f.stat().st_size
            period_stats[sub] = {
                "year_count": len(year_dirs),
                "file_count": file_count,
                "total_bytes": total_bytes,
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


def _safe_code(code: str) -> str:
    """Filesystem-safe version of a stock code (duplicated for warehouse use)."""
    if not code:
        raise ValueError("code cannot be empty")
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in code)


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
