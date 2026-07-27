"""One-off migration from the retired Parquet warehouse to PostgreSQL.

Usage:
    DATABASE_URL=postgresql://... python -m scripts.migrate_parquet_to_postgres \
        --source ./data
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adshare.core.config import get_settings  # noqa: E402
from adshare.historical.warehouse import get_warehouse  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate an adshare Parquet warehouse into PostgreSQL."
    )
    parser.add_argument("--source", type=Path, default=Path("./data"))
    args = parser.parse_args()

    source = args.source.resolve()
    warehouse = get_warehouse(get_settings())

    codes_path = source / "meta" / "codes.parquet"
    if codes_path.exists():
        codes = pd.read_parquet(codes_path)
        print(f"master.stock: {warehouse.upsert_stocks(codes)} rows")

    calendar_path = source / "meta" / "calendar.parquet"
    if calendar_path.exists():
        calendar = pd.read_parquet(calendar_path)
        print(f"market.trade_calendar: {warehouse.upsert_calendar(calendar)} rows")

    for period in ("daily", "weekly", "monthly"):
        period_dir = source / "A_share" / period
        files = sorted(period_dir.glob("*.parquet"))
        written = 0
        for position, path in enumerate(files, start=1):
            written += warehouse.upsert_kline(path.stem, period, pd.read_parquet(path))
            if position % 250 == 0:
                print(f"{period}: {position}/{len(files)} files, {written} rows")
        print(f"{period}: {len(files)} files, {written} rows")

    reference_map = {
        "balance_sheet.parquet": "balance",
        "income.parquet": "income",
        "cashflow.parquet": "cashflow",
        "stk_holdernumber.parquet": "shareholder",
        "index_member.parquet": "index_member",
    }
    for filename, data_type in reference_map.items():
        path = source / "reference" / filename
        if path.exists():
            rows = warehouse.upsert_reference(data_type, pd.read_parquet(path))
            print(f"reference/{data_type}: {rows} rows")

    print("Migration complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
