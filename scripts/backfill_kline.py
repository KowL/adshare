"""Backfill K-line data into the L3 historical warehouse (flat layout).

Usage::

    python -m scripts.backfill_kline --from-date 20200101 --to-date 20260610 --period daily
    python -m scripts.backfill_kline --from-date 20200101 --to-date 20260610 --period all
    python -m scripts.backfill_kline --period daily                       # default window
    python -m scripts.backfill_kline --begin-year 2020 --period daily    # legacy CLI (auto-converted)

By default the script uses the cached ``AmazingData`` adapter to pull data,
then writes the standard per-code Parquet files via
:mod:`amazingdata.batch` (one file per code, all years merged).
The script is intentionally simple — it does not do incremental backfill
or resume: each invocation rewrites the per-stock Parquet files for the
requested window.
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
from amazingdata.batch import (  # noqa: E402
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
        "--from-date",
        type=int,
        default=None,
        help="Inclusive start date YYYYMMDD (default: 20200101).",
    )
    parser.add_argument(
        "--to-date",
        type=int,
        default=None,
        help="Inclusive end date YYYYMMDD (default: today).",
    )
    parser.add_argument(
        "--begin-year",
        type=int,
        default=None,
        help="[legacy] Translate to --from-date=YYYY0101. Use --from-date instead.",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help="[legacy] Translate to --to-date=YYYY1231. Use --to-date instead.",
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
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Number of codes per SDK K-line request. Default: 1, or settings.MAX_CODES_PER_QUERY when all codes.",
    )
    return parser.parse_args()


def _codes_arg(codes: str | None) -> List[str] | None:
    if not codes:
        return None
    return [c.strip() for c in codes.split(",") if c.strip()]


def _resolve_dates(args: argparse.Namespace) -> tuple[int | None, int | None]:
    """Translate legacy ``--begin-year`` / ``--end-year`` to date ints."""
    from_date = args.from_date
    to_date = args.to_date
    if args.begin_year is not None and from_date is None:
        from_date = int(f"{int(args.begin_year)}0101")
        logger.warning("--begin-year is deprecated; prefer --from-date. Using %s", from_date)
    if args.end_year is not None and to_date is None:
        to_date = int(f"{int(args.end_year)}1231")
        logger.warning("--end-year is deprecated; prefer --to-date. Using %s", to_date)
    return from_date, to_date


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
        # Read from local meta/codes.parquet instead of SDK to avoid GIL crashes
        codes_path = warehouse.root / "meta" / "codes.parquet"
        if codes_path.exists():
            import pandas as pd
            df = pd.read_parquet(codes_path)
            code_list = df["code"].tolist()
            print(f"🔎 Backfilling all A-share codes from local cache ({len(code_list)} codes)")
        else:
            print("❌ meta/codes.parquet not found; run sync_meta_codes first")
            return 1

    batch_size = args.batch_size
    if batch_size is None and code_list is None:
        batch_size = int(settings.max_codes_per_query)
    batch_size = max(1, int(batch_size or 1))
    print(f"📚 K-line batch size: {batch_size}")

    from_date, to_date = _resolve_dates(args)
    if from_date is not None or to_date is not None:
        print(f"📅 Pull window: [{from_date or 'default'}, {to_date or 'default'}]")

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
        print(f"\n--- {period} ---")
        t0 = time.time()
        try:
            result = func(
                from_date=from_date,
                to_date=to_date,
                codes=code_list,
                batch_size=batch_size,
            )
        except Exception as e:  # noqa: BLE001
            print(f"   ❌ failed: {e}")
            continue
        print(
            f"   succeeded={result.succeeded} skipped={result.skipped} failed={result.failed} "
            f"rows={result.rows} duration={time.time() - t0:.2f}s"
        )
        if result.errors:
            for err in result.errors[:3]:
                print(f"     • {err}")
            if len(result.errors) > 3:
                print(f"     ... and {len(result.errors) - 3} more")
        results.append(result)

    total_duration = time.time() - started
    kline_results = [r for r in results if r.job.startswith("sync_kline")]
    total_rows = sum(r.rows for r in kline_results)
    total_files = sum(r.succeeded for r in results if r.job.startswith("sync_kline"))
    total_skipped = sum(r.skipped for r in kline_results)
    total_failed = sum(r.failed for r in results)
    print("\n=== Backfill complete ===")
    print(f"   periods={periods} duration={total_duration:.2f}s")
    print(
        f"   kline_rows={total_rows} files_written={total_files} "
        f"skipped={total_skipped} failures={total_failed}"
    )
    return 0 if total_failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
