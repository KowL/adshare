"""Relabel weekly/monthly bars: period-first-day keys -> period-last-trading-day keys.

The AmazingData SDK labels weekly/monthly bars with each stock's *first*
trading day of the period (so suspended stocks drift to mid-week dates).
Since 2026-07-31 the warehouse write path normalizes to the market
convention — the period's *last* trading day (see
``HistoricalWarehouse.upsert_kline`` and ``build_period_end_map``).  This
one-off migration relabels the historical rows written before that change.

The period end is derived from ``market.daily_bar``'s observed trading days
(same source as the write path), *not* ``trade_calendar``, which has been
seen lagging behind in production.

Idempotent: rows already keyed at the period end are untouched, so the
script can be re-run after new old-convention writes (e.g. before the
updated image is deployed).

Usage:
    DATABASE_URL=postgresql://... python -m scripts.migrate_bar_period_end_labels [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adshare.core.config import get_settings  # noqa: E402

_BAR_COLUMNS = (
    "stock_id, stock_code, stock_name, trade_date, open_price, high_price, "
    "low_price, close_price, volume, amount, is_suspended, source_code, "
    "source_updated_at, created_at, updated_at"
)

_TABLES = {
    "week": ("market.weekly_bar", "week"),
    "month": ("market.monthly_bar", "month"),
}


def _setup_temp_tables(cur, table: str, unit: str) -> None:
    """Build mapping (old key -> period end) and the deduplicated move set."""
    cur.execute(
        "CREATE TEMP TABLE _trading_days ON COMMIT DROP AS "
        "SELECT DISTINCT trade_date FROM market.daily_bar"
    )
    cur.execute(
        f"""
        CREATE TEMP TABLE _label_map ON COMMIT DROP AS
        SELECT w.trade_date AS old_date,
               COALESCE((
                   SELECT max(d.trade_date)
                     FROM _trading_days d
                    WHERE date_trunc('{unit}', d.trade_date)
                        = date_trunc('{unit}', w.trade_date)
               ), w.trade_date) AS new_date
          FROM (SELECT DISTINCT trade_date FROM {table}) w
        """
    )
    # Rows whose key changes.  DISTINCT ON keeps the freshest row when two
    # source rows (e.g. a Monday key and a suspension-drift key) collapse
    # onto the same period end.
    cur.execute(
        f"""
        CREATE TEMP TABLE _moved ON COMMIT DROP AS
        SELECT DISTINCT ON (w.stock_id, m.new_date)
               w.stock_id, w.stock_code, w.stock_name,
               w.trade_date AS old_date, m.new_date AS trade_date,
               w.open_price, w.high_price, w.low_price, w.close_price,
               w.volume, w.amount, w.is_suspended, w.source_code,
               w.source_updated_at, w.created_at
          FROM {table} w
          JOIN _label_map m ON m.old_date = w.trade_date
         WHERE m.new_date <> m.old_date
         ORDER BY w.stock_id, m.new_date,
                  w.source_updated_at DESC NULLS LAST,
                  w.updated_at DESC NULLS LAST
        """
    )


def migrate_table(cur, table: str, unit: str, dry_run: bool) -> None:
    cur.execute(f"SELECT count(*) FROM {table}")
    total_before = cur.fetchone()[0]

    _setup_temp_tables(cur, table, unit)

    cur.execute(
        "SELECT count(*), count(*) FILTER (WHERE new_date <> old_date) FROM _label_map"
    )
    distinct_keys, keys_to_change = cur.fetchone()
    cur.execute("SELECT count(*) FROM _moved")
    rows_to_move = cur.fetchone()[0]
    # Source rows that collapse onto the same (stock_id, new key) and lose.
    cur.execute(
        f"""
        SELECT count(*) FROM {table} w
        JOIN _label_map m ON m.old_date = w.trade_date
        WHERE m.new_date <> m.old_date
        """
    )
    rows_under_changing_keys = cur.fetchone()[0]
    # Target keys that already exist (unmoved rows) — freshest row wins.
    cur.execute(
        f"""
        SELECT count(*) FROM _moved mv
        JOIN {table} w
          ON w.stock_id = mv.stock_id AND w.trade_date = mv.trade_date
        """
    )
    key_conflicts = cur.fetchone()[0]

    print(
        f"{table}: {total_before} rows, {distinct_keys} distinct keys, "
        f"{keys_to_change} keys to relabel, {rows_to_move} rows to move "
        f"({rows_under_changing_keys - rows_to_move} duplicates dropped), "
        f"{key_conflicts} conflicts with existing period-end rows"
    )
    if keys_to_change:
        cur.execute(
            "SELECT old_date, new_date FROM _label_map "
            "WHERE new_date <> old_date ORDER BY old_date DESC LIMIT 5"
        )
        for old, new in cur.fetchall():
            print(f"  example: {old} -> {new}")

    if dry_run:
        return

    cur.execute(
        f"""
        DELETE FROM {table} w
        USING _label_map m
        WHERE w.trade_date = m.old_date AND m.new_date <> m.old_date
        """
    )
    deleted = cur.rowcount
    cur.execute(
        f"""
        INSERT INTO {table} ({_BAR_COLUMNS})
        SELECT stock_id, stock_code, stock_name, trade_date,
               open_price, high_price, low_price, close_price,
               volume, amount, is_suspended, source_code,
               source_updated_at, created_at, NOW()
          FROM _moved
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
            source_code = EXCLUDED.source_code,
            source_updated_at = EXCLUDED.source_updated_at,
            updated_at = NOW()
        WHERE {table}.source_updated_at < EXCLUDED.source_updated_at
        """
    )
    print(f"{table}: deleted {deleted} old-key rows, upserted relabeled rows")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Relabel weekly/monthly bars to period-end trade_date."
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg (v3) is required") from exc

    dsn = get_settings().database_url
    with psycopg.connect(dsn, autocommit=False) as conn:
        for table, unit in _TABLES.values():
            with conn.transaction():
                migrate_table(conn.cursor(), table, unit, args.dry_run)
        if args.dry_run:
            conn.rollback()
            print("Dry run — no changes written.")
        else:
            conn.commit()
            print("Migration committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
