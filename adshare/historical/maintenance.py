"""Idempotent maintenance routines for the L3 historical warehouse.

This module consolidates the one-shot repair scripts that were used to
clean up the L3 warehouse into first-class, idempotent functions that
can be invoked from the API, scheduler, or CLI.

All functions are safe to call repeatedly: they detect already-clean
data and return a no-op summary, so they can be wired into the
scheduler or run on demand without operator attention.

Routines provided:

* :func:`repair_kline_directory`  — fill missing ``adj_factor`` with
  1.0 and rewrite ``is_suspended=True`` for OHLCV-zero rows.
* :func:`repair_codes_table`     — drop ``.BJ`` rows from
  ``meta/codes.parquet``.
* :func:`repair_financial_table` — normalise ``report_type`` and dedup
  on the natural key for each financial statement.

Each function returns a :class:`MaintenanceResult` describing the work
performed so the caller (CLI, admin endpoint, scheduler) can log it.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import pandas as pd
import pyarrow.parquet as pq

from adshare.core.logging import get_logger
from adshare.historical.models import (
    KLINE_COLUMNS,
    _financial_dedup_keys,
    _is_sh_sz_code,
    _normalize_financial_df,
    validate_kline_df,
)
from adshare.historical.warehouse import HistoricalWarehouse, get_warehouse

logger = get_logger(__name__)


# ----------------------------------------------------------------------
# Result helpers
# ----------------------------------------------------------------------

@dataclass
class MaintenanceResult:
    """Outcome of a single maintenance operation."""

    job: str
    started_at: float
    finished_at: float = 0.0
    success: bool = False
    files_scanned: int = 0
    files_written: int = 0
    rows_in: int = 0
    rows_out: int = 0
    dropped: int = 0
    mutated: int = 0
    notes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.finished_at - self.started_at

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        return (
            f"{self.job}: scanned={self.files_scanned} "
            f"written={self.files_written} rows_in={self.rows_in} "
            f"rows_out={self.rows_out} dropped={self.dropped} "
            f"mutated={self.mutated} duration={self.duration:.2f}s"
        )


# ----------------------------------------------------------------------
# K-line repair
# ----------------------------------------------------------------------

def _fix_kline_df(df: pd.DataFrame) -> Tuple[pd.DataFrame, int, int, int]:
    """Repair a single K-line DataFrame in-memory.

    Returns ``(cleaned_df, adj_filled, invalid_zero_rows, rows_dropped)``.
    """
    if df is None or df.empty:
        return df, 0, 0, 0

    rows_in = len(df)

    adj_filled = 0
    if "adj_factor" in df.columns:
        n = int(df["adj_factor"].isna().sum())
        if n:
            df = df.copy()
            df.loc[df["adj_factor"].isna(), "adj_factor"] = 1.0
            adj_filled = n

    invalid_before = 0
    if (
        "is_suspended" in df.columns
        and {"open", "high", "low", "close", "volume"} <= set(df.columns)
    ):
        mask = (
            (df["open"] == 0)
            & (df["high"] == 0)
            & (df["low"] == 0)
            & (df["close"] == 0)
            & (df["volume"] == 0)
        )
        invalid_before = int(mask.sum())

    df = validate_kline_df(df)
    rows_dropped = rows_in - len(df)
    return df, adj_filled, invalid_before, rows_dropped


def repair_kline_directory(
    subdirs: Sequence[str] = ("daily", "weekly", "monthly"),
    *,
    dry_run: bool = False,
    warehouse: Optional[HistoricalWarehouse] = None,
) -> MaintenanceResult:
    """Repair every Parquet file under ``A_share/{subdir}/``.

    For each file we:

    1. Fill missing ``adj_factor`` with 1.0 (the AmazingData SDK does
       not currently expose an adjustment factor).
    2. Flip ``is_suspended`` to True and null out prices for rows
       where the upstream pipeline reported ``OHLCV = 0`` on a normal
       trading day (caused by a sync failure that returned 0 for every
       field).

    Files containing only ``.BJ`` (Beijing Stock Exchange) codes are
    skipped — the warehouse no longer serves them.
    """
    warehouse = warehouse or get_warehouse()
    result = MaintenanceResult(
        job="repair_kline_directory", started_at=time.time()
    )
    try:
        for sub in subdirs:
            period_dir = warehouse.kline_dir(sub)
            if not period_dir.exists():
                result.notes.append(f"{sub}: directory missing, skipped")
                continue
            files = sorted(
                f for f in period_dir.glob("*.parquet")
                if not f.stem.endswith(".BJ")
            )
            for f in files:
                result.files_scanned += 1
                try:
                    df = pq.read_table(f).to_pandas()
                except Exception as e:
                    result.errors.append(f"{f.name}: read failed: {e}")
                    continue
                if df.empty:
                    continue
                result.rows_in += len(df)
                cleaned, adj_filled, invalid, dropped = _fix_kline_df(df)
                if cleaned is None or cleaned.empty:
                    result.dropped += len(df)
                    continue
                cleaned = cleaned[
                    [c for c in KLINE_COLUMNS if c in cleaned.columns]
                ]
                result.rows_out += len(cleaned)
                result.dropped += dropped
                mutated = adj_filled + invalid + dropped
                result.mutated += mutated
                if mutated == 0:
                    # Nothing changed: skip the rewrite so the routine
                    # is fully idempotent (a re-run does not touch
                    # mtime and does not invalidate downstream caches).
                    continue
                if dry_run:
                    continue
                try:
                    cleaned.to_parquet(
                        f, engine="pyarrow", compression="zstd", index=False
                    )
                    result.files_written += 1
                except Exception as e:
                    result.errors.append(f"{f.name}: write failed: {e}")
        result.success = not result.errors
    except Exception as e:
        logger.exception("repair_kline_directory failed: %s", e)
        result.errors.append(str(e))
    result.finished_at = time.time()
    logger.info(result.summary())
    return result


# ----------------------------------------------------------------------
# Codes table repair
# ----------------------------------------------------------------------

def repair_codes_table(
    *,
    dry_run: bool = False,
    warehouse: Optional[HistoricalWarehouse] = None,
) -> MaintenanceResult:
    """Drop ``.BJ`` rows from ``meta/codes.parquet``.

    Idempotent: re-running on an already-clean file is a no-op aside
    from the rewrite cost.
    """
    warehouse = warehouse or get_warehouse()
    result = MaintenanceResult(job="repair_codes_table", started_at=time.time())
    try:
        path = warehouse.meta_dir() / "codes.parquet"
        if not path.exists():
            result.errors.append(f"{path}: not found")
            result.finished_at = time.time()
            return result
        result.files_scanned = 1
        df = pq.read_table(path).to_pandas()
        result.rows_in = len(df)
        bj_mask = ~df["code"].astype(str).apply(_is_sh_sz_code)
        bj_count = int(bj_mask.sum())
        if bj_count == 0:
            result.notes.append("no .BJ rows present; nothing to do")
            result.rows_out = result.rows_in
            result.success = True
            result.finished_at = time.time()
            logger.info(result.summary())
            return result
        cleaned = df[~bj_mask].reset_index(drop=True)
        result.rows_out = len(cleaned)
        result.dropped = bj_count
        if bj_count > 0 and not dry_run:
            cleaned.to_parquet(
                path, engine="pyarrow", compression="zstd", index=False
            )
            result.files_written = 1
        result.success = True
    except Exception as e:
        logger.exception("repair_codes_table failed: %s", e)
        result.errors.append(str(e))
    result.finished_at = time.time()
    logger.info(result.summary())
    return result


# ----------------------------------------------------------------------
# Financial table repair
# ----------------------------------------------------------------------

_FINANCIAL_FILES: Tuple[str, ...] = (
    "balance_sheet.parquet",
    "income.parquet",
    "cashflow.parquet",
)


def _pick_code_column(df: pd.DataFrame) -> Optional[str]:
    for cand in ("ts_code", "market_code", "code", "symbol"):
        if cand in df.columns:
            return cand
    return None


def repair_financial_table(
    filenames: Sequence[str] = _FINANCIAL_FILES,
    *,
    dry_run: bool = False,
    warehouse: Optional[HistoricalWarehouse] = None,
) -> MaintenanceResult:
    """Repair each financial table under ``reference/``.

    For every file we:

    1. Drop ``.BJ`` rows (filter on the first available code column).
    2. Normalise ``report_type`` to the canonical ``{1, 2, 3, 4}``
       enum via :func:`_normalize_financial_df`.
    3. Deduplicate exact duplicates on the natural financial key
       ``(ts_code|market_code, reporting_period, report_type,
       statement_type, comp_type_code)``.
    """
    warehouse = warehouse or get_warehouse()
    result = MaintenanceResult(
        job="repair_financial_table", started_at=time.time()
    )
    ref_dir = warehouse.root / "reference"
    try:
        for fname in filenames:
            path = ref_dir / fname
            if not path.exists():
                result.notes.append(f"{fname}: missing, skipped")
                continue
            result.files_scanned += 1
            df = pq.read_table(path).to_pandas()
            result.rows_in += len(df)
            code_col = _pick_code_column(df)
            bj_dropped = 0
            if code_col is not None:
                mask = df[code_col].astype(str).apply(_is_sh_sz_code)
                bj_dropped = int((~mask).sum())
                df = df[mask]
            df = _normalize_financial_df(df, fname.replace(".parquet", ""))
            rt_invalid = (
                int((df["report_type"] == "0").sum())
                if "report_type" in df.columns
                else 0
            )
            dup_cols = _financial_dedup_keys(df)
            n_pre_dedup = len(df)
            if dup_cols:
                df = df.drop_duplicates(subset=dup_cols, keep="last")
            dup_dropped = n_pre_dedup - len(df)
            result.rows_out += len(df)
            result.dropped += bj_dropped + dup_dropped
            changed = (bj_dropped + dup_dropped) > 0
            if bj_dropped:
                result.notes.append(f"{fname}: dropped {bj_dropped} .BJ rows")
            if dup_dropped:
                result.notes.append(
                    f"{fname}: dedup dropped {dup_dropped} exact-duplicate rows"
                )
            if rt_invalid:
                result.notes.append(
                    f"{fname}: {rt_invalid} rows have unrecoverable "
                    f"report_type (kept as '0')"
                )
            if not changed:
                # No row-level change: skip the rewrite so the routine
                # is fully idempotent.
                continue
            if dry_run:
                continue
            try:
                df.to_parquet(
                    path, engine="pyarrow", compression="zstd", index=False
                )
                result.files_written += 1
            except Exception as e:
                result.errors.append(f"{fname}: write failed: {e}")
        result.success = not result.errors
    except Exception as e:
        logger.exception("repair_financial_table failed: %s", e)
        result.errors.append(str(e))
    result.finished_at = time.time()
    logger.info(result.summary())
    return result


# ----------------------------------------------------------------------
# Convenience: run all repairs
# ----------------------------------------------------------------------

def repair_all(
    *,
    dry_run: bool = False,
    warehouse: Optional[HistoricalWarehouse] = None,
) -> List[MaintenanceResult]:
    """Run every repair routine and return the per-job results."""
    return [
        repair_kline_directory(dry_run=dry_run, warehouse=warehouse),
        repair_codes_table(dry_run=dry_run, warehouse=warehouse),
        repair_financial_table(dry_run=dry_run, warehouse=warehouse),
    ]


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m adshare.historical.maintenance",
        description="Idempotent L3 warehouse repair routines.",
    )
    parser.add_argument(
        "job",
        choices=("kline", "codes", "financial", "all"),
        help="Which repair routine to run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read + fix in memory but do not write back.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Override the warehouse root (defaults to settings.historical_path).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.root is not None:
        # Override the historical path for this run only
        os.environ["HISTORICAL_PATH"] = str(args.root.resolve())

    # Force settings to re-read with the override
    from adshare.core.config import get_settings as _get_settings
    _get_settings.cache_clear()

    if args.job == "kline":
        results = [repair_kline_directory(dry_run=args.dry_run)]
    elif args.job == "codes":
        results = [repair_codes_table(dry_run=args.dry_run)]
    elif args.job == "financial":
        results = [repair_financial_table(dry_run=args.dry_run)]
    else:
        results = repair_all(dry_run=args.dry_run)

    rc = 0
    for r in results:
        print(r.summary())
        for note in r.notes:
            print(f"  note: {note}")
        for err in r.errors:
            print(f"  ERROR: {err}")
            rc = 2
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
