"""PostgreSQL-backed market-data repository.

The public class name is intentionally kept as ``HistoricalWarehouse`` so
service and router dependency injection stays stable. The implementation no
longer reads or writes Parquet/DuckDB: AmazingData workers upsert into the
same PostgreSQL database that the API queries.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import pandas as pd

from adshare.core.config import Settings, get_settings
from adshare.core.logging import get_logger
from adshare.historical.models import normalize_period, standardize_kline_df, validate_kline_df

logger = get_logger(__name__)

_BAR_TABLES = {
    "daily": "market.daily_bar",
    "weekly": "market.weekly_bar",
    "monthly": "market.monthly_bar",
}


def _date_from_int(value: Any) -> Optional[date]:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    digits = "".join(ch for ch in str(int(value)) if ch.isdigit())
    if len(digits) != 8 or digits == "00000000":
        return None
    return datetime.strptime(digits, "%Y%m%d").date()


def _clean(value: Any) -> Any:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _exchange_parts(code: str) -> tuple[str, str, str]:
    raw = str(code).strip().upper()
    symbol, _, suffix = raw.partition(".")
    exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(suffix)
    if exchange is None:
        exchange = "SSE" if symbol.startswith(("60", "68", "69")) else "SZSE"
        suffix = "SH" if exchange == "SSE" else "SZ"
        raw = f"{symbol}.{suffix}"
    return raw, symbol, exchange


def _stock_key(code: str) -> str:
    canonical, symbol, exchange = _exchange_parts(code)
    del canonical
    return f"{exchange}.{symbol}"


def _frame(rows: Sequence[Mapping[str, Any]], columns: Optional[Sequence[str]] = None) -> pd.DataFrame:
    df = pd.DataFrame(list(rows), columns=columns)
    for column in df.columns:
        if any(isinstance(v, Decimal) for v in df[column].dropna().head(20)):
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


class HistoricalWarehouse:
    """Connection-pooled PostgreSQL repository used by API and workers."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._max_rows = int(self.settings.database_query_max_rows)
        self._pool = self._create_pool()
        if self.settings.database_auto_migrate:
            self.initialize_schema()

    def _create_pool(self):
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - dependency packaging failure
            raise RuntimeError(
                "PostgreSQL support requires psycopg[binary,pool]"
            ) from exc
        return ConnectionPool(
            conninfo=self.settings.database_url,
            min_size=max(1, int(self.settings.database_pool_min_size)),
            max_size=max(
                int(self.settings.database_pool_min_size),
                int(self.settings.database_pool_max_size),
            ),
            timeout=float(self.settings.database_connect_timeout),
            kwargs={
                "autocommit": False,
                "row_factory": dict_row,
                "connect_timeout": int(self.settings.database_connect_timeout),
                "options": "-c search_path=market,master,public",
            },
            open=True,
        )

    def initialize_schema(self) -> None:
        migration_dir = Path(__file__).parent / "migrations"
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS market")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS market.schema_migration (
                        version     VARCHAR(255) PRIMARY KEY,
                        applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute("SELECT to_regclass('master.stock') AS stock_table")
                stock_schema_exists = cur.fetchone()["stock_table"] is not None
                cur.execute(
                    "SELECT to_regclass('market.adjustment_factor') "
                    "AS factor_table"
                )
                factor_schema_exists = cur.fetchone()["factor_table"] is not None
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'market'
                          AND table_name = 'daily_bar'
                          AND column_name = 'stock_code'
                    ) AS cache_columns
                    """
                )
                cache_schema_exists = cur.fetchone()["cache_columns"]

                # Adopt databases created before explicit migration tracking.
                adopted: list[str] = []
                if stock_schema_exists:
                    adopted.extend(
                        ["001_postgresql.sql", "002_rename_security_to_stock.sql"]
                    )
                if factor_schema_exists:
                    adopted.append("003_adjustment_factor.sql")
                if cache_schema_exists:
                    adopted.append("004_cache_stock_identity_on_bars.sql")
                if adopted:
                    cur.executemany(
                        """
                        INSERT INTO market.schema_migration (version)
                        VALUES (%s)
                        ON CONFLICT (version) DO NOTHING
                        """,
                        [(version,) for version in adopted],
                    )

                cur.execute("SELECT version FROM market.schema_migration")
                applied = {row["version"] for row in cur.fetchall()}
                for migration in sorted(migration_dir.glob("*.sql")):
                    if migration.name in applied:
                        continue
                    cur.execute(migration.read_text(encoding="utf-8"))
                    cur.execute(
                        """
                        INSERT INTO market.schema_migration (version)
                        VALUES (%s)
                        ON CONFLICT (version) DO NOTHING
                        """,
                        (migration.name,),
                    )
            conn.commit()

    def close(self) -> None:
        self._pool.close()

    def refresh_views(self) -> None:
        """Compatibility no-op; PostgreSQL views are migration-managed."""

    def _fetch_df(self, sql: str, params: Sequence[Any] = ()) -> pd.DataFrame:
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SET LOCAL statement_timeout = {int(self.settings.database_query_timeout) * 1000}"
                )
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
        return _frame(rows)

    def _execute_many(self, sql: str, rows: Sequence[Sequence[Any]]) -> int:
        if not rows:
            return 0
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
                affected = cur.rowcount
            conn.commit()
        return max(0, affected)

    # ------------------------------------------------------------------
    # Worker writes
    # ------------------------------------------------------------------

    def upsert_stocks(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        rows: list[tuple[Any, ...]] = []
        for record in df.to_dict("records"):
            code, symbol, exchange = _exchange_parts(str(record.get("code", "")))
            if not symbol:
                continue
            listed = bool(record.get("is_listed", True))
            name = str(record.get("name") or code)
            list_date = _date_from_int(record.get("list_date"))
            delist_date = _date_from_int(record.get("delist_date"))
            rows.append((
                _stock_key(code), code, symbol, exchange, name,
                _clean(record.get("comp_name")), _clean(record.get("comp_sname_eng")),
                _clean(record.get("comp_name_eng")), _clean(record.get("pinyin")),
                _clean(record.get("board")), _clean(record.get("list_plate")),
                _clean(record.get("industry")), list_date, delist_date,
                "LISTED" if listed else "DELISTED",
                name.upper().startswith(("*ST", "ST")),
                datetime.now(timezone.utc),
            ))
        sql = """
            INSERT INTO master.stock (
                stock_key, code, symbol, exchange_code, security_name,
                company_name, security_name_en, company_name_en, pinyin,
                board_type, list_plate, industry, list_date, delist_date,
                listing_status, is_st, source_updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            ON CONFLICT (code) DO UPDATE SET
                stock_key = EXCLUDED.stock_key,
                symbol = EXCLUDED.symbol,
                exchange_code = EXCLUDED.exchange_code,
                security_name = EXCLUDED.security_name,
                company_name = EXCLUDED.company_name,
                security_name_en = EXCLUDED.security_name_en,
                company_name_en = EXCLUDED.company_name_en,
                pinyin = EXCLUDED.pinyin,
                board_type = EXCLUDED.board_type,
                list_plate = EXCLUDED.list_plate,
                industry = EXCLUDED.industry,
                list_date = EXCLUDED.list_date,
                delist_date = EXCLUDED.delist_date,
                listing_status = EXCLUDED.listing_status,
                is_st = EXCLUDED.is_st,
                source_updated_at = EXCLUDED.source_updated_at,
                updated_at = NOW()
        """
        return self._execute_many(sql, rows)

    def upsert_securities(self, df: pd.DataFrame) -> int:
        """Backward-compatible alias for callers migrating to ``upsert_stocks``."""
        return self.upsert_stocks(df)

    def ensure_stock(self, code: str) -> None:
        canonical, symbol, exchange = _exchange_parts(code)
        self._execute_many(
            """
            INSERT INTO master.stock (
                stock_key, code, symbol, exchange_code, security_name
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (code) DO NOTHING
            """,
            [(_stock_key(canonical), canonical, symbol, exchange, canonical)],
        )

    def upsert_kline(self, code: str, period: str, df: pd.DataFrame) -> int:
        std = validate_kline_df(standardize_kline_df(df, code=code))
        if std is None or std.empty:
            return 0
        canonical, _, _ = _exchange_parts(code)
        self.ensure_stock(canonical)
        table = _BAR_TABLES[normalize_period(period)]
        rows: list[tuple[Any, ...]] = []
        now = datetime.now(timezone.utc)
        for record in std.to_dict("records"):
            trade_date = _date_from_int(record.get("date"))
            if trade_date is None:
                continue
            rows.append((
                canonical, trade_date,
                _clean(record.get("open")), _clean(record.get("high")),
                _clean(record.get("low")), _clean(record.get("close")),
                _clean(record.get("volume")), _clean(record.get("amount")),
                bool(record.get("is_suspended", False)), now,
            ))
        sql = f"""
            INSERT INTO {table} (
                stock_id, stock_code, stock_name, trade_date,
                open_price, high_price, low_price,
                close_price, volume, amount, is_suspended,
                source_updated_at
            )
            SELECT s.stock_id, s.code, s.security_name,
                   v.trade_date::DATE, v.open_price::NUMERIC,
                   v.high_price::NUMERIC, v.low_price::NUMERIC,
                   v.close_price::NUMERIC, v.volume::NUMERIC,
                   v.amount::NUMERIC, v.is_suspended::BOOLEAN,
                   v.source_updated_at::TIMESTAMPTZ
            FROM (VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )) AS v(
                code, trade_date, open_price, high_price, low_price,
                close_price, volume, amount, is_suspended, source_updated_at
            )
            JOIN master.stock s ON s.code = v.code
            ON CONFLICT (stock_id, trade_date) DO UPDATE SET
                stock_code = EXCLUDED.stock_code,
                stock_name = EXCLUDED.stock_name,
                open_price = EXCLUDED.open_price,
                high_price = EXCLUDED.high_price,
                low_price = EXCLUDED.low_price,
                close_price = EXCLUDED.close_price,
                volume = EXCLUDED.volume,
                amount = EXCLUDED.amount,
                is_suspended = EXCLUDED.is_suspended,
                source_updated_at = EXCLUDED.source_updated_at,
                updated_at = NOW()
        """
        return self._execute_many(sql, rows)

    def upsert_adjustment_factors(
        self, code: str, factors: pd.DataFrame
    ) -> int:
        if factors is None or factors.empty:
            return 0
        canonical, _, _ = _exchange_parts(code)
        self.ensure_stock(canonical)
        now = datetime.now(timezone.utc)
        rows = []
        for record in factors.to_dict("records"):
            effective_date = _date_from_int(record.get("date"))
            raw_factor = _clean(record.get("adj_factor"))
            try:
                factor = Decimal(str(raw_factor))
            except (ValueError, TypeError):
                continue
            if effective_date is not None and factor > 0:
                rows.append((canonical, effective_date, factor, now))
        return self._execute_many(
            """
            INSERT INTO market.adjustment_factor (
                stock_id, effective_date, adj_factor, source_updated_at
            )
            SELECT s.stock_id, v.effective_date::DATE,
                   v.adj_factor::NUMERIC, v.source_updated_at::TIMESTAMPTZ
            FROM (VALUES (%s, %s, %s, %s)) AS v(
                code, effective_date, adj_factor, source_updated_at
            )
            JOIN master.stock s ON s.code = v.code
            ON CONFLICT (stock_id, effective_date) DO UPDATE SET
                adj_factor = EXCLUDED.adj_factor,
                source_updated_at = EXCLUDED.source_updated_at,
                updated_at = NOW()
            """,
            rows,
        )

    def replace_adjustment_factor_timeline(
        self, code: str, factors: pd.DataFrame
    ) -> int:
        """Reconcile one stock's canonical sparse factor timeline atomically.

        Existing rows are updated in place so their ``created_at`` remains the
        time they first entered the warehouse. Rows no longer present in the
        upstream canonical timeline are removed.
        """
        if factors is None or factors.empty:
            return 0
        canonical, _, _ = _exchange_parts(code)
        self.ensure_stock(canonical)
        now = datetime.now(timezone.utc)
        rows = []
        for record in factors.to_dict("records"):
            effective_date = _date_from_int(record.get("date"))
            raw_factor = _clean(record.get("adj_factor"))
            try:
                factor = Decimal(str(raw_factor))
            except (ValueError, TypeError):
                continue
            if effective_date is not None and factor > 0:
                rows.append((effective_date, factor, now))
        if not rows:
            return 0

        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT stock_id FROM master.stock WHERE code = %s",
                    (canonical,),
                )
                stock = cur.fetchone()
                if stock is None:
                    return 0
                stock_id = stock["stock_id"]
                cur.executemany(
                    """
                    INSERT INTO market.adjustment_factor AS current_factor (
                        stock_id, effective_date, adj_factor, source_updated_at
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (stock_id, effective_date) DO UPDATE SET
                        adj_factor = EXCLUDED.adj_factor,
                        source_updated_at = EXCLUDED.source_updated_at,
                        updated_at = NOW()
                    WHERE current_factor.adj_factor
                          IS DISTINCT FROM EXCLUDED.adj_factor
                    """,
                    [
                        (stock_id, effective_date, factor, source_updated_at)
                        for effective_date, factor, source_updated_at in rows
                    ],
                )
                cur.execute(
                    """
                    DELETE FROM market.adjustment_factor
                     WHERE stock_id = %s
                       AND effective_date <> ALL(%s::DATE[])
                    """,
                    (stock_id, [effective_date for effective_date, _, _ in rows]),
                )
            conn.commit()
        return len(rows)

    def query_codes_without_adjustment_factors(
        self, codes: Optional[Sequence[str]] = None
    ) -> list[str]:
        sql = """
            SELECT s.code
              FROM master.stock s
              LEFT JOIN market.adjustment_factor f
                ON f.stock_id = s.stock_id
             WHERE s.listing_status = 'LISTED'
               AND f.stock_id IS NULL
        """
        params: list[Any] = []
        if codes:
            canonical = [_exchange_parts(code)[0] for code in codes]
            sql += " AND s.code = ANY(%s)"
            params.append(canonical)
        sql += " ORDER BY s.code"
        df = self._fetch_df(sql, params)
        if df.empty:
            return []
        return [str(code) for code in df["code"].tolist()]

    def upsert_calendar(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        now = datetime.now(timezone.utc)
        rows = []
        for record in df.to_dict("records"):
            trade_date = _date_from_int(record.get("date"))
            if trade_date is not None:
                rows.append((
                    trade_date, str(record.get("market") or "SH"),
                    bool(record.get("is_trading_day", True)),
                    int(record.get("weekday", trade_date.weekday())), now,
                ))
        return self._execute_many(
            """
            INSERT INTO market.trade_calendar (
                trade_date, market, is_trading_day, weekday, source_updated_at
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (market, trade_date) DO UPDATE SET
                is_trading_day = EXCLUDED.is_trading_day,
                weekday = EXCLUDED.weekday,
                source_updated_at = EXCLUDED.source_updated_at
            """,
            rows,
        )

    def upsert_reference(self, data_type: str, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("psycopg JSON support is unavailable") from exc
        rows = []
        for record in df.to_dict("records"):
            payload = {str(k): _clean(v) for k, v in record.items()}
            identity_columns = {
                "balance": ("market_code", "ts_code", "code", "reporting_period", "report_type"),
                "income": ("market_code", "ts_code", "code", "reporting_period", "report_type"),
                "cashflow": ("market_code", "ts_code", "code", "reporting_period", "report_type"),
                "shareholder": ("ts_code", "market_code", "code", "end_date"),
                "index_member": ("index_code", "con_code", "in_date", "out_date"),
            }.get(data_type, ())
            identity = {
                key: payload.get(key)
                for key in identity_columns
                if payload.get(key) is not None
            }
            encoded = json.dumps(
                identity or payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            natural_key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            code = next(
                (payload.get(k) for k in ("market_code", "ts_code", "code", "con_code") if payload.get(k)),
                None,
            )
            raw_date = next(
                (payload.get(k) for k in ("reporting_period", "end_date", "trade_date", "date") if payload.get(k)),
                None,
            )
            rows.append((data_type, natural_key, code, _date_from_int(raw_date), Jsonb(payload)))
        return self._execute_many(
            """
            INSERT INTO market.reference_data (
                data_type, natural_key, code, reference_date, payload
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (data_type, natural_key) DO UPDATE SET
                code = EXCLUDED.code,
                reference_date = EXCLUDED.reference_date,
                payload = EXCLUDED.payload,
                source_updated_at = NOW()
            """,
            rows,
        )

    def start_sync_job(
        self,
        *,
        job_name: str,
        data_type: str,
        sync_mode: str = "INCREMENTAL",
    ) -> Optional[int]:
        """Insert a RUNNING row at the start of a sync and return its id.

        Returns ``None`` if the insert failed for any reason (DB outage,
        unique-violation, etc.). Callers should treat a ``None`` return
        as "could not track this run" rather than as a hard failure —
        the sync itself should still proceed and the completion update
        can fall back to ``record_sync_job`` which inserts the row
        directly if the start row never landed.
        """
        sql = """
            INSERT INTO market.sync_job (
                job_name, data_type, source_code, sync_mode, job_status
            ) VALUES (%s, %s, 'AMAZINGDATA', %s, 'RUNNING')
            RETURNING id
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (job_name, data_type, sync_mode))
                    row = cur.fetchone()
                conn.commit()
            return int(row["id"]) if row else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to start sync_job %s/%s: %s", job_name, data_type, exc)
            return None

    def record_sync_job(
        self,
        *,
        job_name: Optional[str] = None,
        data_type: Optional[str] = None,
        status: str,
        sync_mode: str = "INCREMENTAL",
        range_start: Optional[int] = None,
        range_end: Optional[int] = None,
        records_read: int = 0,
        records_inserted: int = 0,
        records_failed: int = 0,
        started_at: Optional[float] = None,
        error_message: Optional[str] = None,
        job_id: Optional[int] = None,
    ) -> int:
        """Finalize a sync_job row.

        With ``job_id``: UPDATE the existing row to its terminal status
        (``SUCCESS`` / ``PARTIAL_SUCCESS`` / ``FAILED`` / ``CANCELLED``).
        Without ``job_id`` (legacy callers / start row was lost):
        INSERT a terminal row, preserving the original started_at if
        supplied.
        """
        if job_id is not None:
            sql = """
                UPDATE market.sync_job SET
                    job_status = %s,
                    range_start = COALESCE(%s, range_start),
                    range_end = COALESCE(%s, range_end),
                    records_read = %s,
                    records_inserted = %s,
                    records_failed = %s,
                    completed_at = NOW(),
                    error_message = %s
                WHERE id = %s
            """
            params = (
                status,
                _date_from_int(range_start),
                _date_from_int(range_end),
                int(records_read),
                int(records_inserted),
                int(records_failed),
                error_message,
                int(job_id),
            )
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    affected = cur.rowcount
                conn.commit()
            if affected > 0:
                return int(job_id)
            # The start row vanished (manual delete? concurrency?).
            # Fall through to INSERT below so we never lose the audit
            # trail of a completed sync.
            logger.warning(
                "start_sync_job row id=%s missing at finalize; falling back to INSERT",
                job_id,
            )

        started = datetime.fromtimestamp(started_at, timezone.utc) if started_at else datetime.now(timezone.utc)
        return self._execute_many(
            """
            INSERT INTO market.sync_job (
                job_name, data_type, sync_mode, job_status, range_start,
                range_end, records_read, records_inserted, records_failed,
                started_at, completed_at, error_message
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            """,
            [(
                job_name, data_type, sync_mode, status,
                _date_from_int(range_start), _date_from_int(range_end),
                int(records_read), int(records_inserted), int(records_failed),
                started, error_message,
            )],
        )

    # ------------------------------------------------------------------
    # API reads
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
        if not codes:
            return pd.DataFrame()
        table = _BAR_TABLES[normalize_period(period)]
        canonical = [_exchange_parts(code)[0] for code in codes]
        sql = f"""
            SELECT b.stock_code AS code,
                   b.stock_name AS name,
                   TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
                   b.open_price AS open, b.high_price AS high,
                   b.low_price AS low, b.close_price AS close,
                   b.volume, b.amount, af.adj_factor, b.is_suspended,
                   EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at
              FROM {table} b
              LEFT JOIN LATERAL (
                  SELECT f.adj_factor
                    FROM market.adjustment_factor f
                   WHERE f.stock_id = b.stock_id
                     AND f.effective_date <= b.trade_date
                   ORDER BY f.effective_date DESC
                   LIMIT 1
              ) af ON TRUE
             WHERE b.stock_code = ANY(%s)
               AND b.trade_date BETWEEN %s AND %s
             ORDER BY b.stock_code, b.trade_date
        """
        params: list[Any] = [
            canonical, _date_from_int(begin_date), _date_from_int(end_date)
        ]
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([int(limit), int(offset)])
        return self._fetch_df(sql, params)

    def query_calendar(
        self,
        market: Optional[str] = None,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        sql = """
            SELECT TO_CHAR(trade_date, 'YYYYMMDD')::INTEGER AS date,
                   market, is_trading_day, weekday,
                   EXTRACT(EPOCH FROM source_updated_at)::BIGINT AS sync_at
              FROM market.trade_calendar
             WHERE 1=1
        """
        params: list[Any] = []
        if market:
            sql += " AND market = %s"
            params.append(market)
        if begin_date is not None:
            sql += " AND trade_date >= %s"
            params.append(_date_from_int(begin_date))
        if end_date is not None:
            sql += " AND trade_date <= %s"
            params.append(_date_from_int(end_date))
        sql += " ORDER BY trade_date"
        return self._fetch_df(sql, params)

    def query_adjustment_factors(
        self,
        codes: Sequence[str],
        begin_date: int,
        end_date: int,
    ) -> pd.DataFrame:
        if not codes:
            return pd.DataFrame()
        canonical = [_exchange_parts(code)[0] for code in codes]
        return self._fetch_df(
            """
            SELECT b.stock_code AS code,
                   TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
                   af.adj_factor,
                   EXTRACT(EPOCH FROM af.source_updated_at)::BIGINT AS sync_at
              FROM market.daily_bar b
              LEFT JOIN LATERAL (
                  SELECT f.adj_factor, f.source_updated_at
                    FROM market.adjustment_factor f
                   WHERE f.stock_id = b.stock_id
                     AND f.effective_date <= b.trade_date
                   ORDER BY f.effective_date DESC
                   LIMIT 1
              ) af ON TRUE
             WHERE b.stock_code = ANY(%s)
               AND b.trade_date BETWEEN %s AND %s
             ORDER BY b.stock_code, b.trade_date
            """,
            [
                canonical,
                _date_from_int(begin_date),
                _date_from_int(end_date),
            ],
        )

    def query_codes(
        self,
        board: Optional[str] = None,
        is_listed: Optional[bool] = None,
    ) -> pd.DataFrame:
        sql = """
            SELECT code, security_name AS name, company_name AS comp_name,
                   pinyin, company_name_en AS comp_name_eng,
                   security_name_en AS comp_sname_eng,
                   COALESCE(TO_CHAR(list_date, 'YYYYMMDD')::INTEGER, 0) AS list_date,
                   COALESCE(TO_CHAR(delist_date, 'YYYYMMDD')::INTEGER, 0) AS delist_date,
                   listing_status = 'LISTED' AS is_listed,
                   board_type AS board, list_plate, industry,
                   EXTRACT(EPOCH FROM source_updated_at)::BIGINT AS sync_at
              FROM master.stock
             WHERE 1=1
        """
        params: list[Any] = []
        if board:
            sql += " AND board_type = %s"
            params.append(board)
        if is_listed is not None:
            sql += " AND (listing_status = 'LISTED') = %s"
            params.append(bool(is_listed))
        sql += " ORDER BY code"
        return self._fetch_df(sql, params)

    def _query_reference(
        self,
        data_type: str,
        code: Optional[str] = None,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        sql = "SELECT payload FROM market.reference_data WHERE data_type = %s"
        params: list[Any] = [data_type]
        if code:
            sql += " AND code = %s"
            params.append(code)
        if begin_date is not None:
            sql += " AND reference_date >= %s"
            params.append(_date_from_int(begin_date))
        if end_date is not None:
            sql += " AND reference_date <= %s"
            params.append(_date_from_int(end_date))
        rows = self._fetch_df(sql, params)
        if rows.empty:
            return pd.DataFrame()
        return pd.DataFrame(rows["payload"].tolist())

    def query_financial(
        self,
        statement_type: str,
        ts_code: Optional[str] = None,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        kind = {
            "balance_sheet": "balance",
            "balance": "balance",
            "income": "income",
            "cashflow": "cashflow",
        }.get(statement_type, statement_type)
        return self._query_reference(kind, ts_code, begin_date, end_date)

    def query_shareholder(
        self,
        ts_code: Optional[str] = None,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        return self._query_reference("shareholder", ts_code, begin_date, end_date)

    def query_index_member(
        self,
        index_code: Optional[str] = None,
        ts_code: Optional[str] = None,
    ) -> pd.DataFrame:
        df = self._query_reference("index_member", ts_code)
        if index_code and not df.empty and "index_code" in df.columns:
            df = df[df["index_code"].astype(str) == str(index_code)]
        return df.reset_index(drop=True)

    def is_synced(
        self,
        begin_date: int,
        end_date: int,
        period: str,
        codes: Optional[Sequence[str]] = None,
    ) -> bool:
        if not codes:
            return self.max_trade_date(period) is not None
        df = self.query_kline(codes, begin_date, end_date, period)
        if df.empty:
            return False
        requested = {_exchange_parts(code)[0] for code in codes}
        return requested.issubset(set(df["code"].astype(str).unique()))

    def max_trade_date(self, period: str) -> Optional[int]:
        table = _BAR_TABLES[normalize_period(period)]
        df = self._fetch_df(
            f"SELECT TO_CHAR(MAX(trade_date), 'YYYYMMDD')::INTEGER AS value FROM {table}"
        )
        if df.empty or pd.isna(df.iloc[0]["value"]):
            return None
        return int(df.iloc[0]["value"])

    def execute_sql(self, sql: str, max_rows: Optional[int] = None) -> pd.DataFrame:
        if not sql or not sql.strip():
            raise ValueError("empty SQL statement")
        cleaned = sql.strip().rstrip(";")
        head = cleaned.lstrip("(").split(None, 1)[0].upper()
        if head not in {"SELECT", "WITH"}:
            raise ValueError("only SELECT/CTE statements are allowed")
        forbidden = {
            "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "COPY",
            "GRANT", "REVOKE", "TRUNCATE", "CALL", "DO", "SET", "RESET",
        }
        tokens = {token.strip("(),;").upper() for token in cleaned.split()}
        hit = forbidden & tokens
        if hit:
            raise ValueError(f"statement '{sorted(hit)[0]}' is not allowed")
        cap = max(1, min(int(max_rows or self._max_rows), self._max_rows))
        return self._fetch_df(f"SELECT * FROM ({cleaned}) AS q LIMIT %s", [cap + 1])

    def stats(self) -> Dict[str, Any]:
        periods: Dict[str, Any] = {}
        for name, table in _BAR_TABLES.items():
            df = self._fetch_df(
                f"""
                SELECT COUNT(*) AS total_rows,
                       COUNT(DISTINCT stock_id) AS code_count,
                       TO_CHAR(MIN(trade_date), 'YYYYMMDD')::INTEGER AS first_date,
                       TO_CHAR(MAX(trade_date), 'YYYYMMDD')::INTEGER AS last_date
                  FROM {table}
                """
            )
            periods[name] = df.iloc[0].to_dict() if not df.empty else {}
        return {"backend": "postgresql", "periods": periods}

    def freshness_stats(self, period: str) -> Optional[Dict[str, Any]]:
        table = _BAR_TABLES[normalize_period(period)]
        df = self._fetch_df(
            f"""
            SELECT TO_CHAR(MAX(trade_date), 'YYYYMMDD') AS latest_trade_date,
                   COUNT(DISTINCT stock_id) AS code_count,
                   EXTRACT(EPOCH FROM MAX(source_updated_at))::BIGINT AS last_sync_at
              FROM {table}
            """
        )
        if df.empty or not df.iloc[0]["latest_trade_date"]:
            return None
        row = df.iloc[0]
        return {
            "latest_trade_date": str(row["latest_trade_date"]),
            "latest_complete_date": str(row["latest_trade_date"]),
            "code_count": int(row["code_count"] or 0),
            "last_sync_at": int(row["last_sync_at"]) if not pd.isna(row["last_sync_at"]) else None,
        }

    def health(self) -> Dict[str, Any]:
        try:
            df = self._fetch_df("SELECT 1 AS ok")
            connected = bool(not df.empty and int(df.iloc[0]["ok"]) == 1)
            error = None
        except Exception as exc:  # noqa: BLE001
            connected = False
            error = str(exc)
        return {
            "historical_enabled": self.settings.historical_enabled,
            "backend": "postgresql",
            "database_connected": connected,
            "database": self.settings.database_url.rsplit("@", 1)[-1],
            "error": error,
        }


_warehouse: Optional[HistoricalWarehouse] = None
_warehouse_lock = threading.Lock()


def get_warehouse(settings: Optional[Settings] = None) -> HistoricalWarehouse:
    global _warehouse
    if _warehouse is None:
        with _warehouse_lock:
            if _warehouse is None:
                _warehouse = HistoricalWarehouse(settings=settings)
    return _warehouse


def reset_warehouse() -> None:
    global _warehouse
    with _warehouse_lock:
        if _warehouse is not None:
            try:
                _warehouse.close()
            finally:
                _warehouse = None
