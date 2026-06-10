"""Migrate L3 historical warehouse from year-bucketed layout to flat layout.

OLD layout::

    A_share/daily/{YYYY}/{code}.parquet
    A_share/weekly/{YYYY}/{code}.parquet
    A_share/monthly/{YYYY}/{code}.parquet

NEW layout (after migration)::

    A_share/daily/{code}.parquet     # all years merged
    A_share/daily/_metadata.json
    A_share/weekly/{code}.parquet
    A_share/weekly/_metadata.json
    A_share/monthly/{code}.parquet
    A_share/monthly/_metadata.json

Usage::

    python -m scripts.migrate_to_flat_layout --dry-run
    python -m scripts.migrate_to_flat_layout                 # default: do all 3 periods
    python -m scripts.migrate_to_flat_layout --period daily
    python -m scripts.migrate_to_flat_layout --keep-old      # leave year dirs in place
    python -m scripts.migrate_to_flat_layout --backup-root data_backup_{ts}

Behavior:

* Reads every year-bucketed file under ``A_share/{period}/{YYYY}/{code}.parquet``
* Groups by code, concatenates all years, deduplicates on ``date`` (keep last),
  sorts ascending, validates.
* Writes ``A_share/{period}/{code}.parquet`` (zstd, pyarrow).
* Writes ``A_share/{period}/_metadata.json`` (per-period summary).
* Removes the old year directories (unless ``--keep-old`` is set).
* If ``--backup-root`` is set, moves the old year directories there first.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

# Allow running from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from adshare.core.config import get_settings  # noqa: E402
from adshare.core.logging import setup_logging, get_logger  # noqa: E402
from adshare.historical.models import (  # noqa: E402
    KLINE_COLUMNS,
    standardize_kline_df,
    validate_kline_df,
    write_metadata,
)
from adshare.historical.warehouse import get_warehouse  # noqa: E402

logger = get_logger("migrate_flat_layout")


def _list_year_dirs(period_dir: Path) -> List[Path]:
    if not period_dir.exists():
        return []
    out: List[Path] = []
    for child in sorted(period_dir.iterdir()):
        if child.is_dir() and child.name.isdigit() and len(child.name) == 4:
            out.append(child)
    return out


def _group_by_code(year_dirs: Iterable[Path]) -> Dict[str, List[Path]]:
    """Return {code: [year_dir/{code}.parquet, ...]} for every code present."""
    out: Dict[str, List[Path]] = defaultdict(list)
    for year_dir in year_dirs:
        for f in sorted(year_dir.glob("*.parquet")):
            out[f.stem].append(f)
    return out


def _merge_one(code: str, files: List[Path]) -> pd.DataFrame:
    """Load all year files for a code, concat, dedupe, sort, validate."""
    frames: List[pd.DataFrame] = []
    for f in files:
        try:
            df = pd.read_parquet(f)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to read %s: %s; skipping", f, e)
            continue
        if df.empty:
            continue
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=KLINE_COLUMNS)
    merged = pd.concat(frames, ignore_index=True)
    # Coerce date and drop dupes (keep last; multiple syncs may have updated).
    if "date" in merged.columns:
        merged["date"] = pd.to_numeric(merged["date"], errors="coerce").fillna(0).astype("int64")
        merged = merged.drop_duplicates(subset=["date"], keep="last")
        merged = merged.sort_values("date").reset_index(drop=True)
    std = standardize_kline_df(merged, code=code)
    std = validate_kline_df(std)
    return std


def migrate_period(
    root: Path,
    period: str,
    *,
    dry_run: bool = False,
    keep_old: bool = False,
    backup_root: Path | None = None,
) -> dict:
    period_dir = root / "A_share" / period
    year_dirs = _list_year_dirs(period_dir)
    if not year_dirs:
        return {"period": period, "year_dirs": 0, "codes": 0, "rows": 0, "files_written": 0}

    by_code = _group_by_code(year_dirs)
    total_rows = 0
    files_written = 0
    first_date: int | None = None
    last_date: int | None = None
    last_sync_at = int(time.time())

    print(f"  📂 {period}: {len(year_dirs)} year dirs, {len(by_code)} unique codes")

    if dry_run:
        # Just count rows to size up the operation.
        for code, files in by_code.items():
            n = 0
            for f in files:
                try:
                    n += len(pd.read_parquet(f, columns=["date"]))
                except Exception:  # noqa: BLE001
                    pass
            total_rows += n
        return {
            "period": period,
            "year_dirs": len(year_dirs),
            "codes": len(by_code),
            "rows": total_rows,
            "files_written": 0,
        }

    for code, files in by_code.items():
        std = _merge_one(code, files)
        if std.empty:
            continue
        # Track date range for the metadata sidecar.
        if "date" in std.columns and not std.empty:
            lo, hi = int(std["date"].min()), int(std["date"].max())
            if first_date is None or lo < first_date:
                first_date = lo
            if last_date is None or hi > last_date:
                last_date = hi
        out_path = period_dir / f"{code}.parquet"
        std.to_parquet(out_path, engine="pyarrow", compression="zstd", index=False)
        files_written += 1
        total_rows += len(std)
        if files_written % 500 == 0:
            print(f"     … {files_written}/{len(by_code)} written")

    # Per-period metadata sidecar.
    write_metadata(
        root,
        period,
        file_count=files_written,
        total_rows=total_rows,
        first_date=first_date,
        last_date=last_date,
        last_sync_at=last_sync_at,
    )

    if not keep_old:
        # Optional backup move.
        if backup_root is not None:
            backup_period = backup_root / "A_share" / period
            backup_period.mkdir(parents=True, exist_ok=True)
        for year_dir in year_dirs:
            if backup_root is not None:
                dst = backup_root / "A_share" / period / year_dir.name
                shutil.move(str(year_dir), str(dst))
            else:
                shutil.rmtree(year_dir, ignore_errors=True)

    return {
        "period": period,
        "year_dirs": len(year_dirs),
        "codes": len(by_code),
        "rows": total_rows,
        "files_written": files_written,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate L3 warehouse to flat layout.")
    parser.add_argument(
        "--period",
        choices=("daily", "weekly", "monthly", "all"),
        default="all",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count files and rows without writing anything.",
    )
    parser.add_argument(
        "--keep-old",
        action="store_true",
        help="Leave the year directories in place after writing flat files.",
    )
    parser.add_argument(
        "--backup-root",
        type=str,
        default=None,
        help="Move old year directories under this directory instead of deleting them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging()
    settings = get_settings()
    warehouse = get_warehouse(settings)
    root = warehouse.root

    print(f"📦 Warehouse root: {root}")
    periods = ["daily", "weekly", "monthly"] if args.period == "all" else [args.period]
    backup_root = Path(args.backup_root).resolve() if args.backup_root else None

    started = time.time()
    summaries: List[dict] = []
    for period in periods:
        print(f"\n--- {period} ---")
        summary = migrate_period(
            root,
            period,
            dry_run=args.dry_run,
            keep_old=args.keep_old,
            backup_root=backup_root,
        )
        summaries.append(summary)

    total_duration = time.time() - started
    print("\n=== Migration summary ===")
    print(
        f"{'period':<8} {'year_dirs':>10} {'codes':>8} {'rows':>10} {'written':>10} {'duration':>10}"
    )
    print("-" * 60)
    for s in summaries:
        print(
            f"{s['period']:<8} {s['year_dirs']:>10} {s['codes']:>8} "
            f"{s['rows']:>10} {s['files_written']:>10} {'':>10}"
        )
    print(f"\nTotal duration: {total_duration:.2f}s")
    if args.dry_run:
        print("(dry-run: no files were written and no directories were modified)")
    elif args.keep_old:
        print("(keep-old: year directories left in place)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
