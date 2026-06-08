"""Backfill 5 years of K-line data into the L3 historical warehouse.

Usage::

    python -m scripts.backfill_kline --begin-year 2021 --end-year 2025 --period daily
    python -m scripts.backfill_kline --begin-year 2021 --end-year 2025 --period all
    python -m scripts.backfill_kline --begin-year 2024 --end-year 2024 --period weekly
    python -m scripts.backfill_kline --begin-year 2024 --end-year 2024 --period monthly

By default the script uses the cached ``AmazingData`` adapter to pull data,
then writes the standard Parquet files via
:mod:`adshare.historical.sync`. The script is intentionally simple — it does
not do incremental backfill or resume: each invocation rewrites the per-stock
Parquet files for the requested years.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List

# Allow running as ``python scripts/backfill_kline.py``
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adshare.core.config import get_settings  # noqa: E402
from adshare.core.logging import setup_logging, get_logger  # noqa: E402
from adshare.historical.sync import (  # noqa: E402
    SyncResult,
    sync_kline_daily,
    sync_kline_weekly,
    sync_kline_monthly,
    sync_meta_codes,
    sync_meta_calendar,
)
from adshare.historical.warehouse import get_warehouse  # noqa: E402

logger = get_logger("backfill_kline")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill K-line data into the L3 warehouse.")
    parser.add_argument(
        "--begin-year",
        type=int,
        default=2021,
        help="First calendar year to backfill (inclusive).",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=2025,
        help="Last calendar year to backfill (inclusive).",
    )
    parser.add_argument(
        "--period",
        choices=("daily", "weekly", "monthly", "all"),
        default="daily",
        help="K-line period to backfill.",
    )
    parser.add_argument(
        "--codes",
        type=str,
        default=None,
        help="Optional comma-separated list of codes. Default: all A-share codes.",
    )
    parser.add_argument(
        "--meta",
        action="store_true",
        help="Also refresh meta/codes.parquet and meta/calendar.parquet before backfilling.",
    )
    parser.add_argument(
        "--market",
        type=str,
        default="SH",
        help="Market code for the calendar sync (default: SH).",
    )
    return parser.parse_args()


def _codes_arg(codes: str | None) -> List[str] | None:
    if not codes:
        return None
    return [c.strip() for c in codes.split(",") if c.strip()]


def main() -> int:
    args = parse_args()
    setup_logging()
    settings = get_settings()

    if not settings.historical_enabled:
        print("❌ HISTORICAL_ENABLED is false; refusing to backfill.")
        return 1

    warehouse = get_warehouse(settings)
    print(f"📦 Warehouse root: {warehouse.root}")

    code_list = _codes_arg(args.codes)
    if code_list:
        print(f"🔎 Restricting to {len(code_list)} codes")
    else:
        print("🔎 Backfilling all A-share codes")

    begin_year = max(1990, int(args.begin_year))
    end_year = max(begin_year, int(args.end_year))
    years = list(range(begin_year, end_year + 1))

    periods = ["daily", "weekly", "monthly"] if args.period == "all" else [args.period]
    period_to_func = {
        "daily": sync_kline_daily,
        "weekly": sync_kline_weekly,
        "monthly": sync_kline_monthly,
    }

    started = time.time()
    results: List[SyncResult] = []

    if args.meta:
        print("\n--- meta/codes.parquet ---")
        results.append(sync_meta_codes())
        print(f"   rows={results[-1].rows} success={results[-1].success}")

        print(f"\n--- meta/calendar.parquet (market={args.market}) ---")
        results.append(sync_meta_calendar(market=args.market))
        print(f"   rows={results[-1].rows} success={results[-1].success}")

    for period in periods:
        func = period_to_func[period]
        for year in years:
            print(f"\n--- {period}/{year} ---")
            t0 = time.time()
            try:
                result = func(year=year, codes=code_list)
            except Exception as e:  # noqa: BLE001
                print(f"   ❌ failed: {e}")
                continue
            print(
                f"   succeeded={result.succeeded} failed={result.failed} "
                f"rows={result.rows} duration={time.time() - t0:.2f}s"
            )
            if result.errors:
                for err in result.errors[:3]:
                    print(f"     • {err}")
                if len(result.errors) > 3:
                    print(f"     ... and {len(result.errors) - 3} more")
            results.append(result)

    total_duration = time.time() - started
    total_rows = sum(r.rows for r in results)
    total_files = sum(r.succeeded for r in results if r.job.startswith("sync_kline"))
    total_failed = sum(r.failed for r in results)
    print("\n=== Backfill complete ===")
    print(f"   years={years} periods={periods} duration={total_duration:.2f}s")
    print(f"   total_rows={total_rows} files_written={total_files} failures={total_failed}")
    return 0 if total_failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
