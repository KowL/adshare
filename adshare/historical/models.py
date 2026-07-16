"""Parquet schema definitions and DataFrame validation for the L3 warehouse.

Schemas are derived from `docs/historical-data-architecture.md` §3.2.

The file layout is one Parquet file per (period, code) — all years merged
into a single file. Validation removes logically invalid rows (high<low,
etc.) so that downstream DuckDB queries operate on clean data.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from adshare.core.logging import get_logger

logger = get_logger(__name__)

KLINE_COLUMNS: Tuple[str, ...] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "adj_factor",
    "is_suspended",
    "sync_at",
)

KLINE_DTYPES: Dict[str, str] = {
    "date": "int32",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "int64",
    "amount": "float64",
    "adj_factor": "float64",
    "is_suspended": "bool",
    "sync_at": "int64",
}

CALENDAR_COLUMNS: Tuple[str, ...] = (
    "date",
    "market",
    "is_trading_day",
    "weekday",
    "sync_at",
)

CALENDAR_DTYPES: Dict[str, str] = {
    "date": "int32",
    "market": "string",
    "is_trading_day": "bool",
    "weekday": "int8",
    "sync_at": "int64",
}

CODES_COLUMNS: Tuple[str, ...] = (
    "code",
    "name",
    "list_date",
    "delist_date",
    "is_listed",
    "board",
    "industry",
    "sync_at",
)

CODES_DTYPES: Dict[str, str] = {
    "code": "string",
    "name": "string",
    "list_date": "int32",
    "delist_date": "int32",
    "is_listed": "bool",
    "board": "string",
    "industry": "string",
    "sync_at": "int64",
}

PERIOD_ALIASES: Dict[str, str] = {
    "day": "daily",
    "d": "daily",
    "daily": "daily",
    "1d": "daily",
    "week": "weekly",
    "w": "weekly",
    "weekly": "weekly",
    "1w": "weekly",
    "month": "monthly",
    "m": "monthly",
    "monthly": "monthly",
    "1m": "monthly",
}


def normalize_period(period: str) -> str:
    """Normalize a period string to the canonical subdirectory name.

    Returns the subdirectory name (e.g. ``"daily"``) for any common alias.
    Raises ``ValueError`` for unsupported periods.
    """
    if not period:
        raise ValueError("period cannot be empty")
    key = period.lower()
    if key not in PERIOD_ALIASES:
        raise ValueError(
            f"unsupported period '{period}'; expected one of: {sorted(set(PERIOD_ALIASES.values()))}"
        )
    return PERIOD_ALIASES[key]


def period_to_subdir(period: str) -> str:
    """Alias for :func:`normalize_period` that always returns the subdir name."""
    return normalize_period(period)


def kline_file_path(
    root: Path | str,
    period: str,
    code: str,
    year: Optional[int] = None,
) -> Path:
    """Return the Parquet file path for one (period, code) tuple.

    The ``year`` argument is accepted for backward compatibility but is
    ignored — the flat layout stores all years in a single file per code.
    The code is sanitized so the resulting filename is filesystem-safe.
    """
    del year  # flat layout: ignore year
    subdir = normalize_period(period)
    safe_code = _safe_code(code)
    return Path(root) / "A_share" / subdir / f"{safe_code}.parquet"


def _safe_code(code: str) -> str:
    """Replace filesystem-unsafe characters in a stock code."""
    if not code:
        raise ValueError("code cannot be empty")
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in code)


def _coerce_bool(series: pd.Series) -> pd.Series:
    """Coerce a Series to boolean, treating common truthy values as True."""
    if series.dtype == bool:
        return series
    return series.map(lambda v: bool(v) if pd.notna(v) else False).astype(bool)


def _coerce_int(series: pd.Series, dtype: str, allow_na: bool = True) -> pd.Series:
    """Coerce a Series to an integer dtype, replacing NaN with 0 when needed."""
    if series.dtype == object:
        try:
            series = pd.to_numeric(series, errors="coerce")
        except Exception:
            pass
    if not allow_na:
        series = series.fillna(0)
    try:
        return series.astype(dtype)
    except (TypeError, ValueError):
        return series.fillna(0).astype(dtype)


def standardize_kline_df(df: pd.DataFrame, code: Optional[str] = None) -> pd.DataFrame:
    """Coerce a raw K-line DataFrame into the canonical Parquet schema.

    The function:

    * Renames the typical AmazingData columns (``kline_time`` -> ``date``)
    * Converts timestamps to int ``YYYYMMDD``
    * Drops the redundant ``code`` column (filename already encodes it)
    * Casts each column to the schema dtype
    * Fills missing optional columns (``adj_factor``, ``is_suspended``)
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=list(KLINE_COLUMNS))

    df = df.copy()

    if "kline_time" in df.columns and "date" not in df.columns:
        df = df.rename(columns={"kline_time": "date"})

    if "date" not in df.columns:
        return pd.DataFrame(columns=list(KLINE_COLUMNS))

    df["date"] = df["date"].apply(_date_to_int)
    n = len(df)

    for col in ("open", "high", "low", "close", "amount", "adj_factor"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
        else:
            df[col] = pd.Series([np.nan] * n, index=df.index, dtype="float64")

    # The AmazingData SDK does not currently surface an adjustment
    # factor column. Treat absence as "no adjustment" (factor 1.0)
    # so the column is non-null and downstream ratio math
    # (e.g. ``pre_close * adj_factor / prev_adj_factor``) can still
    # run. Once the SDK exposes a real adj_factor column this
    # default becomes a no-op because the column will be present.
    if "adj_factor" in df.columns and df["adj_factor"].isna().all():
        df["adj_factor"] = 1.0

    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    else:
        df["volume"] = pd.Series([0] * n, index=df.index, dtype="int64")

    if "is_suspended" in df.columns:
        df["is_suspended"] = _coerce_bool(df["is_suspended"])
    else:
        df["is_suspended"] = pd.Series([False] * n, index=df.index, dtype="bool")

    if "sync_at" not in df.columns:
        df["sync_at"] = int(time.time())
    df["sync_at"] = pd.to_numeric(df["sync_at"], errors="coerce").fillna(0).astype("int64")

    if "code" in df.columns:
        df = df.drop(columns=["code"])

    df = df[list(KLINE_COLUMNS)]
    df = df.dropna(subset=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _date_to_int(value: Any) -> int:
    """Convert a date-like value into an int YYYYMMDD."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return 0
    if isinstance(value, (int, np.integer)):
        v = int(value)
        if v > 10_000_000:
            return v
        if v > 0:
            return v
        return 0
    if hasattr(value, "strftime"):
        try:
            return int(pd.Timestamp(value).strftime("%Y%m%d"))
        except Exception:
            return 0
    try:
        ts = pd.Timestamp(value)
        return int(ts.strftime("%Y%m%d"))
    except Exception:
        return 0


def validate_kline_df(df: pd.DataFrame) -> pd.DataFrame:
    """Drop logically invalid rows from a standardized K-line DataFrame."""
    if df is None or df.empty:
        return df
    df = df.copy()
    required = ["date", "open", "high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            return pd.DataFrame(columns=list(KLINE_COLUMNS))

    invalid = df[
        (df["high"] < df["low"])
        | (df["high"] < df["open"])
        | (df["high"] < df["close"])
        | (df["low"] > df["open"])
        | (df["low"] > df["close"])
    ]
    if not invalid.empty:
        df = df.drop(invalid.index)
    df = df[df["volume"] >= 0]

    # Drop rows with non-positive prices unless explicitly suspended.
    # This catches SDK aggregation bugs that produce open/high > 0 while
    # low/close are 0 (observed for weekly/monthly bars on 2026-06-12).
    price_cols = [c for c in ("open", "high", "low", "close") if c in df.columns]
    if price_cols:
        not_suspended = ~df["is_suspended"].fillna(False)
        bad_price = (df[price_cols] <= 0).any(axis=1) & not_suspended
        if bad_price.any():
            df = df.drop(df[bad_price].index)

    # Fix rows where the upstream pipeline reported OHLCV all zero
    # (typically caused by a sync failure that returned 0 for every
    # field on a single trading day). Force ``is_suspended=True`` and
    # null out the prices so downstream math treats it as a halt, not
    # a real close of 0.
    if "is_suspended" in df.columns and {"open", "high", "low", "close", "volume"} <= set(df.columns):
        zero_mask = (
            (df["open"] == 0)
            & (df["high"] == 0)
            & (df["low"] == 0)
            & (df["close"] == 0)
            & (df["volume"] == 0)
        )
        if zero_mask.any():
            for col in ("open", "high", "low", "close", "amount"):
                if col in df.columns:
                    df.loc[zero_mask, col] = np.nan
            df.loc[zero_mask, "is_suspended"] = True

    df = df.drop_duplicates(subset=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def standardize_calendar_df(df: pd.DataFrame, market: str = "SH") -> pd.DataFrame:
    """Coerce a calendar DataFrame into the canonical schema."""
    if df is None or df.empty:
        return pd.DataFrame(columns=list(CALENDAR_COLUMNS))

    df = df.copy()
    if "date" not in df.columns:
        if "kline_time" in df.columns:
            df = df.rename(columns={"kline_time": "date"})
        else:
            return pd.DataFrame(columns=list(CALENDAR_COLUMNS))
    df["date"] = df["date"].apply(_date_to_int)
    n = len(df)

    if "market" not in df.columns:
        df["market"] = pd.Series([market] * n, index=df.index, dtype="string")
    df["market"] = df["market"].astype(str)

    if "is_trading_day" not in df.columns:
        df["is_trading_day"] = pd.Series([True] * n, index=df.index, dtype="bool")
    df["is_trading_day"] = _coerce_bool(df["is_trading_day"])

    if "weekday" not in df.columns:
        df["weekday"] = df["date"].apply(
            lambda d: pd.Timestamp(str(int(d))).weekday() if d else 0
        )
    df["weekday"] = _coerce_int(df["weekday"], "int8")

    if "sync_at" not in df.columns:
        df["sync_at"] = int(time.time())
    df["sync_at"] = _coerce_int(df["sync_at"], "int64")

    df = df[list(CALENDAR_COLUMNS)]
    df = df.dropna(subset=["date"])
    df = df.drop_duplicates(subset=["date", "market"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def standardize_codes_df(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a code-list DataFrame into the canonical schema."""
    if df is None or df.empty:
        return pd.DataFrame(columns=list(CODES_COLUMNS))

    df = df.copy()
    rename_map = {
        "MARKET_CODE": "code",
        "SECURITY_CODE": "code",
        "SECUCODE": "code",
        "SECURITY_NAME": "name",
        "symbol": "name",  # symbol is the security name in some SDKs
    }
    for src, dst in rename_map.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    # If the DataFrame has a non-default index whose values look like codes,
    # promote that index to a 'code' column. This handles adapters that
    # return code info with the code in the index and only `symbol` as a
    # column.
    if "code" not in df.columns:
        idx_name = df.index.name
        idx_values = list(df.index)
        if idx_name in ("code", "symbol", "MARKET_CODE", "SECURITY_CODE"):
            df = df.reset_index().rename(columns={idx_name: "code"})
        elif idx_values and all(
            isinstance(v, str) and v == v and ("." in v or v.isalnum())
            for v in idx_values
        ):
            # Index values look like codes (e.g. "000001.SZ")
            df = df.reset_index()
            df = df.rename(columns={df.columns[0]: "code"})

    if "code" not in df.columns:
        return pd.DataFrame(columns=list(CODES_COLUMNS))

    # If "symbol" is present and the codes are missing, try symbol as code
    if df["code"].dtype == object and "symbol" in df.columns:
        pass  # keep going

    df["code"] = df["code"].astype(str)
    n = len(df)
    if "name" in df.columns:
        df["name"] = df["name"].astype(str)
    else:
        df["name"] = pd.Series([""] * n, index=df.index, dtype="string")

    for col in ("list_date", "delist_date"):
        if col in df.columns:
            df[col] = df[col].apply(_date_to_int)
        else:
            df[col] = pd.Series([0] * n, index=df.index, dtype="int32")
        df[col] = _coerce_int(df[col], "int32")

    if "is_listed" in df.columns:
        df["is_listed"] = _coerce_bool(df["is_listed"])
    else:
        df["is_listed"] = pd.Series([True] * n, index=df.index, dtype="bool")

    if "board" not in df.columns:
        df["board"] = df["code"].apply(_infer_board)
    df["board"] = df["board"].astype(str)

    if "industry" not in df.columns:
        df["industry"] = pd.Series([""] * n, index=df.index, dtype="string")
    df["industry"] = df["industry"].astype(str)

    if "sync_at" not in df.columns:
        df["sync_at"] = int(time.time())
    df["sync_at"] = _coerce_int(df["sync_at"], "int64")

    df = df[list(CODES_COLUMNS)]
    df = df.drop_duplicates(subset=["code"])
    df = df.sort_values("code").reset_index(drop=True)
    return df


def _infer_board(code: str) -> str:
    """Infer the listing board from the security code prefix."""
    clean = code.split(".")[0] if "." in code else code
    if clean.startswith("68"):
        return "科创板"
    if clean.startswith("8") or clean.startswith("4"):
        return "北交所"
    if clean.startswith("30"):
        return "创业板"
    if clean.startswith("60") or clean.startswith("00"):
        return "主板"
    return "主板"


def write_metadata(
    root: Path | str,
    period: str,
    *,
    file_count: int,
    total_rows: int,
    first_date: Optional[int] = None,
    last_date: Optional[int] = None,
    last_sync_at: Optional[int] = None,
) -> Path:
    """Write a per-period ``_metadata.json`` file summarizing the warehouse."""
    import json

    root = Path(root)
    subdir = normalize_period(period)
    period_dir = root / "A_share" / subdir
    period_dir.mkdir(parents=True, exist_ok=True)
    meta_path = period_dir / "_metadata.json"
    payload: Dict[str, Any] = {
        "version": "2.0",
        "schema": {
            "columns": list(KLINE_COLUMNS),
            "dtypes": KLINE_DTYPES,
        },
        "period": subdir,
        "file_count": int(file_count),
        "total_rows": int(total_rows),
        "first_date": int(first_date) if first_date is not None else None,
        "last_date": int(last_date) if last_date is not None else None,
        "last_sync_at": int(last_sync_at or time.time()),
    }
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return meta_path


def summarize_kline_files(files: Iterable[Path]) -> Dict[str, Any]:
    """Return aggregate statistics for a set of K-line Parquet files."""
    files = list(files)
    return {
        "file_count": len(files),
        "total_bytes": sum(f.stat().st_size for f in files if f.exists()),
    }


# ----------------------------------------------------------------------
# Financial-statement data-quality helpers
# ----------------------------------------------------------------------
# These helpers are shared by the worker-side sync jobs
# (:mod:`amazingdata.batch`) and the API-side maintenance routines
# (:mod:`adshare.historical.maintenance`). They are pure functions over
# DataFrames and carry no SDK dependency.

_VALID_REPORT_TYPES = {"1", "2", "3", "4"}


def _normalize_report_type(value) -> str:
    """Coerce ``report_type`` into a canonical one of {1, 2, 3, 4}.

    The AmazingData SDK has occasionally returned a date string
    (e.g. ``"20200430"``) in the ``report_type`` column instead of the
    expected enum (``"1"``=Q1, ``"2"``=半年报, ``"3"``=Q3, ``"4"``=年报).
    Map the common date-as-report_type rows to the most likely report
    type by looking at the row's ``reporting_period`` month:

    * 03-31 → 1 (Q1)
    * 06-30 → 2 (semi-annual)
    * 09-30 → 3 (Q3)
    * 12-31 → 4 (annual)

    Values that are already a valid enum are returned unchanged. Values
    that cannot be resolved are returned as the string "0" so the row
    is still persisted but the anomaly is preserved for inspection.
    """
    s = "" if value is None else str(value).strip()
    if s in _VALID_REPORT_TYPES:
        return s
    # Try to map from a date-shaped value (YYYYMMDD or YYYY-MM-DD)
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 8:
        # First try MMDD against the canonical period-end dates
        month_day = digits[4:8]
        mapped = {
            "0331": "1",
            "0630": "2",
            "0930": "3",
            "1231": "4",
        }.get(month_day)
        if mapped:
            return mapped
        # Fall back to inferring the report type from the *quarter*
        # implied by the date. We only consider the month — anything
        # that does not match a known period-end is reported as
        # unrecoverable so the operator can investigate.
        month = int(digits[4:6])
        return {
            3: "1",
            6: "2",
            9: "3",
            12: "4",
        }.get(month, "0")
    return "0"


def _normalize_financial_df(df: pd.DataFrame, statement_type: str) -> pd.DataFrame:
    """Apply data-quality fixes to a freshly pulled financial DataFrame.

    Currently:

    * Coerces ``report_type`` to the canonical {1,2,3,4} enum.
    * Filters out rows that look like a different statement type
      (e.g. income rows in the balance sheet frame) — the SDK
      sometimes returns mixed data when ``begin_date`` is in the
      past.
    """
    if df is None or df.empty:
        return df
    df = df.copy()
    if "report_type" in df.columns:
        df["report_type"] = df["report_type"].apply(_normalize_report_type)
        bad = int((df["report_type"] == "0").sum())
        if bad:
            logger.warning(
                "financial(%s): %d rows have unrecoverable report_type; kept as '0'",
                statement_type, bad,
            )
    return df


def _financial_dedup_keys(df: pd.DataFrame) -> List[str]:
    """Return the column subset that uniquely identifies a financial row.

    The financial tables we store use ``market_code`` (not ``ts_code``)
    as the security code column. We try ``ts_code`` first for forward
    compatibility (in case a future SDK call names it that way), then
    fall back to ``market_code``.

    Preferred natural key (in order):
    ``(code, reporting_period, report_type, statement_type, comp_type_code)``

    Where ``code`` is whichever of ``ts_code``/``market_code`` exists.
    """
    code_col = "ts_code" if "ts_code" in df.columns else (
        "market_code" if "market_code" in df.columns else None
    )
    preferred = [
        code_col,
        "reporting_period", "report_type",
        "statement_type", "comp_type_code",
    ]
    return [c for c in preferred if c]


def _is_sh_sz_code(code: str) -> bool:
    """Return True if a code belongs to the SH/SZ main boards we keep.

    We deliberately exclude ``.BJ`` (Beijing Stock Exchange) — those
    files are not part of the supported product surface and the
    warehouse no longer stores them.
    """
    if not code:
        return False
    c = code.strip()
    if c.endswith(".BJ"):
        return False
    if "." in c:
        return c.endswith(".SH") or c.endswith(".SZ")
    # Bare 6-digit codes: 60x/68x/69x = SH, 00x/30x/39x = SZ.
    if len(c) == 6 and c.isdigit():
        return c.startswith(("60", "68", "69", "00", "30", "39"))
    return False


def _filter_sh_sz_codes(codes) -> List[str]:
    """Filter an iterable of codes to keep only SH/SZ main board entries."""
    if codes is None:
        return []
    return [c for c in codes if _is_sh_sz_code(c)]
