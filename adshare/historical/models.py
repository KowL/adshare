"""Parquet schema definitions and DataFrame validation for the L3 warehouse.

Schemas are derived from `docs/historical-data-architecture.md` §3.2.

The file layout is one Parquet file per (period, year, code). Validation
removes logically invalid rows (high<low, etc.) so that downstream DuckDB
queries operate on clean data.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
import pandas as pd

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
    year: int,
    code: str,
) -> Path:
    """Return the Parquet file path for one (period, year, code) tuple.

    The code is sanitized so the resulting filename is filesystem-safe.
    """
    subdir = normalize_period(period)
    safe_code = _safe_code(code)
    return Path(root) / "A_share" / subdir / str(int(year)) / f"{safe_code}.parquet"


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
    year: int,
    *,
    file_count: int,
    total_rows: int,
    last_sync_at: Optional[int] = None,
) -> Path:
    """Write a ``_metadata.json`` file describing a year's sync status."""
    import json

    root = Path(root)
    subdir = normalize_period(period)
    year_dir = root / "A_share" / subdir / str(int(year))
    year_dir.mkdir(parents=True, exist_ok=True)
    meta_path = year_dir / "_metadata.json"
    payload: Dict[str, Any] = {
        "version": "1.0",
        "schema": {
            "columns": list(KLINE_COLUMNS),
            "dtypes": KLINE_DTYPES,
        },
        "year": int(year),
        "period": subdir,
        "file_count": int(file_count),
        "total_rows": int(total_rows),
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
