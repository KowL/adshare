"""Repair existing K-line Parquet files by re-running validation.

Usage::

    python -m scripts.repair_kline_quality --period daily
    python -m scripts.repair_kline_quality --period weekly
    python -m scripts.repair_kline_quality --period monthly
    python -m scripts.repair_kline_quality --period all

The script reads every per-code Parquet file, drops logically invalid rows
(non-positive prices on non-suspended bars, OHLC inconsistencies, all-zero
rows that should be marked suspended), deduplicates by date, and writes the
cleaned file back.  It is idempotent: running it twice produces the same
output.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple

# Allow running as ``python scripts/repair_kline_quality.py``
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from adshare.core.config import get_settings  # noqa: E402
from adshare.core.logging import setup_logging, get_logger  # noqa: E402
from adshare.historical.models import validate_kline_df, normalize_period  # noqa: E402

logger = get_logger("repair_kline_quality")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair K-line Parquet files by re-running validation."
    )
    parser.add_argument(
        "--period",
        choices=("daily", "weekly", "monthly", "all"),
        default="all",
        help="K-line period to repair (default: all).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count affected rows/files without writing changes.",
    )
    return parser.parse_args()


def _repair_file(path: Path, dry_run: bool) -> Tuple[Path, int, int, int]:
    """Repair a single Parquet file.

    Returns:
        (path, original_rows, cleaned_rows, rows_dropped)
    """
    try:
        df = pd.read_parquet(path)
    except Exception as e:
        logger.warning("Cannot read %s: %s", path, e)
        return path, 0, 0, 0

    if df.empty:
        return path, 0, 0, 0

    original_rows = len(df)
    cleaned = validate_kline_df(df)
    cleaned_rows = len(cleaned)
    dropped = original_rows - cleaned_rows

    if dropped and not dry_run:
        try:
            cleaned.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
        except Exception as e:
            logger.warning("Cannot write %s: %s", path, e)
            return path, original_rows, cleaned_rows, 0

    return path, original_rows, cleaned_rows, dropped


def repair_period(period: str, workers: int, dry_run: bool) -> dict:
    """Repair all files for one period."""
    settings = get_settings()
    root = Path(settings.historical_path).resolve()
    subdir = root / "A_share" / normalize_period(period)
    if not subdir.exists():
        logger.warning("Directory missing: %s", subdir)
        return {"files": 0, "dropped": 0, "cleaned": 0}

    files = sorted(subdir.glob("*.parquet"))
    if not files:
        logger.warning("No parquet files found in %s", subdir)
        return {"files": 0, "dropped": 0, "cleaned": 0}

    total_original = 0
    total_cleaned = 0
    files_changed = 0
    files_processed = 0

    start = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_repair_file, f, dry_run): f for f in files}
        for fut in as_completed(futures):
            path, original_rows, cleaned_rows, dropped = fut.result()
            files_processed += 1
            total_original += original_rows
            total_cleaned += cleaned_rows
            if dropped:
                files_changed += 1
                logger.info(
                    "%s: dropped %d rows (%d -> %d)",
                    path.name, dropped, original_rows, cleaned_rows,
                )

    duration = time.time() - start
    logger.info(
        "Period=%s files=%d changed=%d rows=%d -> %d (dropped=%d) duration=%.2fs",
        period,
        files_processed,
        files_changed,
        total_original,
        total_cleaned,
        total_original - total_cleaned,
        duration,
    )
    return {
        "files": files_processed,
        "changed": files_changed,
        "dropped": total_original - total_cleaned,
    }


def main() -> int:
    setup_logging()
    args = parse_args()

    periods: List[str]
    if args.period == "all":
        periods = ["daily", "weekly", "monthly"]
    else:
        periods = [args.period]

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info("Starting K-line quality repair (%s)", mode)

    total = {"files": 0, "changed": 0, "dropped": 0}
    for period in periods:
        result = repair_period(period, args.workers, args.dry_run)
        for key in total:
            total[key] += result.get(key, 0)

    logger.info(
        "Total: files=%d changed=%d dropped=%d (%s)",
        total["files"],
        total["changed"],
        total["dropped"],
        mode,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
