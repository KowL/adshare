"""Derived metric calculations for Pro-style stock data APIs.

Handles price change computations, adjustment factors, moving averages,
and suspension derivation from raw K-line DataFrames.
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd


def compute_price_changes(df: pd.DataFrame) -> pd.DataFrame:
    """Compute pre_close, change, pct_chg.

    Pro data platform returns rows in descending trade_date order
    (newest first). pre_close is the previous trading day's close.

    Args:
        df: DataFrame with at least 'close' and 'date' columns.
            Expected in ascending date order from warehouse.

    Returns:
        DataFrame with additional pre_close, change, pct_chg columns,
        sorted by date descending (newest first).
    """
    if df is None or df.empty:
        return df

    df = df.copy()

    # Ensure ascending order for shift calculation
    df = df.sort_values("date", ascending=True).reset_index(drop=True)

    # pre_close is the close from the previous row (previous trading day)
    df["pre_close"] = df["close"].shift(1)

    # change and pct_chg
    df["change"] = df["close"] - df["pre_close"]
    df["pct_chg"] = (df["change"] / df["pre_close"] * 100).round(2)

    # Sort descending (newest first) — Pro platform convention
    df = df.sort_values("date", ascending=False).reset_index(drop=True)

    return df


def apply_adjustment(
    df: pd.DataFrame,
    adj_df: pd.DataFrame,
    adj_type: str,
) -> pd.DataFrame:
    """Apply forward or backward adjustment to OHLC prices.

    Args:
        df: K-line DataFrame with open/high/low/close/price columns.
        adj_df: DataFrame with 'date' and 'adj_factor' columns.
        adj_type: 'qfq' (forward) or 'hfq' (backward).

    Returns:
        DataFrame with adjusted prices.
    """
    if df is None or df.empty or adj_df is None or adj_df.empty:
        return df

    df = df.copy()

    # Drop existing adj_factor to avoid _x/_y suffixes during merge
    df = df.drop(columns=["adj_factor"], errors="ignore")

    # Merge adj_factor
    df = df.merge(adj_df[["date", "adj_factor"]], on="date", how="left")
    df["adj_factor"] = df["adj_factor"].ffill()

    price_cols = ["open", "high", "low", "close"]

    if adj_type == "qfq":
        # Forward adjustment: latest date's factor is the base (1.0)
        df_sorted = df.sort_values("date", ascending=True).reset_index(drop=True)
        base_factor = df_sorted["adj_factor"].iloc[-1]
        for col in price_cols:
            if col in df.columns:
                df[col] = (df[col] * df["adj_factor"] / base_factor).round(2)
    elif adj_type == "hfq":
        # Backward adjustment: multiply by factor directly
        for col in price_cols:
            if col in df.columns:
                df[col] = (df[col] * df["adj_factor"]).round(2)

    return df.drop(columns=["adj_factor"])


def compute_moving_averages(
    df: pd.DataFrame,
    ma_params: List[int],
) -> pd.DataFrame:
    """Compute rolling moving averages on the close price.

    Args:
        df: DataFrame with 'close' and 'date' columns.
        ma_params: List of MA windows, e.g. [5, 10, 20].

    Returns:
        DataFrame with additional ma{N} columns.
    """
    if df is None or df.empty or not ma_params:
        return df

    df = df.copy()

    # Sort ascending for rolling calculation
    df = df.sort_values("date", ascending=True).reset_index(drop=True)

    for n in ma_params:
        col_name = f"ma{n}"
        df[col_name] = df["close"].rolling(window=n, min_periods=1).mean().round(2)

    # Return to descending order
    df = df.sort_values("date", ascending=False).reset_index(drop=True)

    return df


def derive_suspensions(df: pd.DataFrame) -> pd.DataFrame:
    """Derive suspension records from K-line data.

    Identifies consecutive trading days where volume == 0 as suspension periods.

    Args:
        df: DataFrame with 'code', 'date', 'volume' columns.

    Returns:
        DataFrame with columns: ts_code, suspend_date, resume_date.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["ts_code", "suspend_date", "resume_date"])

    df = df.copy()
    df = df.sort_values("date", ascending=True).reset_index(drop=True)

    # Mark suspension: volume is 0 or NaN
    df["is_suspend"] = (df["volume"] == 0) | (df["volume"].isna())

    # Group consecutive suspensions
    df["group"] = (df["is_suspend"] != df["is_suspend"].shift()).cumsum()

    # Aggregate suspension groups
    suspends = []
    for _, group_df in df[df["is_suspend"]].groupby("group"):
        code = str(group_df["code"].iloc[0])
        suspend_date = int(group_df["date"].min())
        resume_date = int(group_df["date"].max())

        # Find the next trading day after suspension ends
        last_suspend_idx = group_df.index[-1]
        next_day = df.loc[df.index > last_suspend_idx]
        if not next_day.empty:
            resume_date = int(next_day.iloc[0]["date"])
        else:
            # Suspension continues to the end of data range
            resume_date = None

        suspends.append({
            "ts_code": code,
            "suspend_date": suspend_date,
            "resume_date": resume_date,
        })

    return pd.DataFrame(suspends)


def convert_volume_to_lots(df: pd.DataFrame) -> pd.DataFrame:
    """Convert volume from shares (股) to lots (手 = 100 shares).

    Pro data platform uses 'vol' in lots (手) while adshare warehouse
    stores volume in shares (股).

    Args:
        df: DataFrame with 'volume' column.

    Returns:
        DataFrame with 'vol' column (lots) and 'volume' dropped.
    """
    if df is None or df.empty:
        return df

    df = df.copy()
    if "volume" in df.columns:
        df["vol"] = (df["volume"] / 100).round(0).astype(int)
    return df


def map_stock_basic_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Map adshare warehouse codes columns to Pro platform stock_basic fields.

    Args:
        df: DataFrame from warehouse.query_codes().

    Returns:
        DataFrame with Pro platform field names and computed columns.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "ts_code", "symbol", "name", "area", "industry", "fullname",
            "enname", "cnspell", "market", "exchange", "curr_type",
            "list_status", "list_date", "delist_date", "is_hs",
        ])

    df = df.copy()

    # Core mappings
    df["ts_code"] = df["code"].astype(str)
    df["symbol"] = df["code"].astype(str).str.split(".").str[0]
    df["name"] = df.get("name", "")

    # Missing fields — return empty/defaults
    df["area"] = ""
    df["fullname"] = df.get("comp_name", "")
    df["enname"] = ""
    df["cnspell"] = ""
    df["curr_type"] = "CNY"
    df["is_hs"] = "N"

    # Industry from warehouse if available
    df["industry"] = df.get("industry", "")

    # Market / exchange from code suffix
    def _exchange_from_code(code: str) -> str:
        if code.endswith(".SH"):
            return "SSE"
        if code.endswith(".SZ"):
            return "SZSE"
        if code.endswith(".BJ"):
            return "BSE"
        return ""

    def _market_from_board(board: str) -> str:
        mapping = {
            "主板": "主板",
            "创业板": "创业板",
            "科创板": "科创板",
            "北交所": "北交所",
            "CDR": "CDR",
        }
        return mapping.get(str(board), str(board)) if pd.notna(board) else ""

    df["exchange"] = df["ts_code"].apply(_exchange_from_code)
    if "board" in df.columns:
        df["market"] = df["board"].apply(_market_from_board)
    else:
        df["market"] = ""

    # List status
    def _list_status(is_listed):
        if pd.isna(is_listed):
            return "L"
        return "L" if int(is_listed) == 1 else "D"

    if "is_listed" in df.columns:
        df["list_status"] = df["is_listed"].apply(_list_status)
    else:
        df["list_status"] = "L"
    df["list_date"] = df.get("list_date", None)
    df["delist_date"] = df.get("delist_date", None)

    # Select and order output columns
    cols = [
        "ts_code", "symbol", "name", "area", "industry", "fullname",
        "enname", "cnspell", "market", "exchange", "curr_type",
        "list_status", "list_date", "delist_date", "is_hs",
    ]
    return df[[c for c in cols if c in df.columns]]


def map_trade_cal_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Map adshare warehouse calendar columns to Pro platform trade_cal fields.

    Args:
        df: DataFrame from warehouse.query_calendar().

    Returns:
        DataFrame with Pro platform field names.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["exchange", "cal_date", "is_open", "pretrade_date"])

    df = df.copy()
    df = df.sort_values("date", ascending=True).reset_index(drop=True)

    df["cal_date"] = df["date"]

    # is_open from warehouse uses is_trading_day or is_open
    if "is_trading_day" in df.columns:
        df["is_open"] = df["is_trading_day"].apply(lambda x: 1 if x else 0)
    elif "is_open" in df.columns:
        df["is_open"] = df["is_open"].apply(lambda x: 1 if x else 0)
    else:
        df["is_open"] = 1

    # pretrade_date: previous trading day
    trading_days = df[df["is_open"] == 1]["date"].tolist()
    pretrade_map = {}
    for i, d in enumerate(trading_days):
        pretrade_map[d] = trading_days[i - 1] if i > 0 else None

    df["pretrade_date"] = df["date"].map(pretrade_map)

    # exchange mapping
    def _exchange(mkt):
        if pd.isna(mkt):
            return ""
        m = str(mkt).upper()
        if m in ("SH", "SSE"):
            return "SSE"
        if m in ("SZ", "SZSE"):
            return "SZSE"
        if m in ("BJ", "BSE"):
            return "BSE"
        return m

    if "market" in df.columns:
        df["exchange"] = df["market"].apply(_exchange)
    else:
        df["exchange"] = ""

    cols = ["exchange", "cal_date", "is_open", "pretrade_date"]
    return df[[c for c in cols if c in df.columns]]


def map_kline_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Map adshare K-line columns to Pro platform daily/weekly/monthly fields.

    Args:
        df: DataFrame from warehouse.query_kline(), already processed by
            compute_price_changes and convert_volume_to_lots.

    Returns:
        DataFrame with Pro platform field names.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "ts_code", "trade_date", "open", "high", "low", "close",
            "pre_close", "change", "pct_chg", "vol", "amount",
        ])

    df = df.copy()

    # Rename core columns
    rename_map = {}
    if "code" in df.columns:
        rename_map["code"] = "ts_code"
    if "date" in df.columns:
        rename_map["date"] = "trade_date"

    if rename_map:
        df = df.rename(columns=rename_map)

    # Ensure ts_code is string
    if "ts_code" in df.columns:
        df["ts_code"] = df["ts_code"].astype(str)

    # Ensure trade_date is int/YYYYMMDD
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_numeric(df["trade_date"], errors="coerce").fillna(0).astype(int)

    return df


def map_adj_factor_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Map adj_factor columns to Pro platform format."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])

    df = df.copy()
    rename_map = {}
    if "code" in df.columns:
        rename_map["code"] = "ts_code"
    if "date" in df.columns:
        rename_map["date"] = "trade_date"
    if rename_map:
        df = df.rename(columns=rename_map)

    if "ts_code" in df.columns:
        df["ts_code"] = df["ts_code"].astype(str)
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_numeric(df["trade_date"], errors="coerce").fillna(0).astype(int)
    if "adj_factor" in df.columns:
        df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce")

    cols = ["ts_code", "trade_date", "adj_factor"]
    return df[[c for c in cols if c in df.columns]]


def map_suspend_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Map suspension columns to Pro platform suspend_d fields."""
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "ts_code", "suspend_date", "resume_date",
            "ann_date", "suspend_reason", "reason_type",
        ])

    df = df.copy()
    df["ann_date"] = None
    df["suspend_reason"] = ""
    df["reason_type"] = ""

    cols = ["ts_code", "suspend_date", "resume_date", "ann_date", "suspend_reason", "reason_type"]
    return df[[c for c in cols if c in df.columns]]


def build_limit_list(up_items: list, down_items: list, trade_date: int) -> pd.DataFrame:
    """Build Pro-style limit_list DataFrame from LimitUpItem and LimitDownItem.

    Args:
        up_items: List of LimitUpItem objects.
        down_items: List of LimitDownItem objects.
        trade_date: Target trade date as int YYYYMMDD.

    Returns:
        DataFrame with Pro platform limit_list fields.
    """
    rows = []

    def _ts_code_suffix(code: str) -> str:
        if not code:
            return ".SZ"
        if code.startswith(("60", "68", "88", "89")):
            return ".SH"
        if code.startswith(("8", "4", "92", "93")):
            return ".BJ"
        return ".SZ"

    def _add(items, limit_flag):
        for item in items:
            code = str(getattr(item, "code", ""))
            row = {
                "ts_code": f"{code}{_ts_code_suffix(code)}",
                "trade_date": int(trade_date),
                "name": getattr(item, "name", ""),
                "close": getattr(item, "price", 0.0),
                "pct_chg": round(getattr(item, "changePct", 0.0) * 100, 2),
                "amp": getattr(item, "amplitude", 0.0),
                "fc_ratio": None,
                "fl_ratio": None,
                "fd_amount": None,
                "first_time": getattr(item, "firstTime", "") or "",
                "last_time": getattr(item, "finalTime", "") or "",
                "open_times": None,
                "up_stat": f"{getattr(item, 'limitUpDays', 1)}连板" if limit_flag == "U" else "",
                "limit": limit_flag,
                "swing": None,
                "board": getattr(item, "board", ""),
                "volume": getattr(item, "volume", 0),
                "amount": getattr(item, "amount", 0.0),
                "pre_close": getattr(item, "preClose", 0.0),
            }
            rows.append(row)

    _add(up_items, "U")
    _add(down_items, "D")

    df = pd.DataFrame(rows)
    cols = [
        "ts_code", "trade_date", "name", "close", "pct_chg", "amp",
        "fc_ratio", "fl_ratio", "fd_amount", "first_time", "last_time",
        "open_times", "up_stat", "limit", "swing", "board",
        "volume", "amount", "pre_close",
    ]
    return df[[c for c in cols if c in df.columns]]


def filter_new_shares(df: pd.DataFrame, list_date: int) -> pd.DataFrame:
    """Filter stock_basic DataFrame to new shares listed on/after list_date.

    Args:
        df: DataFrame from map_stock_basic_fields.
        list_date: Reference date int YYYYMMDD.

    Returns:
        DataFrame with new_share fields.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "ts_code", "sub_code", "name", "ipo_date", "issue_date",
            "amount", "market_amount", "price", "pe",
            "limit_amount", "funds", "ballot",
        ])

    df = df.copy()
    df["list_date_int"] = pd.to_numeric(df["list_date"], errors="coerce").fillna(0).astype(int)
    df = df[df["list_date_int"] >= int(list_date)]

    # New share fields — only ts_code/name/ipo_date available from basic info
    df["ts_code"] = df["ts_code"].astype(str)
    df["sub_code"] = df["symbol"].astype(str)
    df["ipo_date"] = df["list_date_int"]
    df["issue_date"] = df["list_date_int"]
    df["amount"] = None
    df["market_amount"] = None
    df["price"] = None
    df["pe"] = None
    df["limit_amount"] = None
    df["funds"] = None
    df["ballot"] = None

    cols = [
        "ts_code", "sub_code", "name", "ipo_date", "issue_date",
        "amount", "market_amount", "price", "pe",
        "limit_amount", "funds", "ballot",
    ]
    return df[[c for c in cols if c in df.columns]]


def filter_fields(df: pd.DataFrame, fields: Optional[str]) -> pd.DataFrame:
    """Filter DataFrame to only specified fields.

    Args:
        df: Input DataFrame.
        fields: Comma-separated field names, or None to return all.

    Returns:
        DataFrame with only the requested columns.
    """
    if not fields or df is None or df.empty:
        return df

    wanted = [f.strip() for f in fields.split(",") if f.strip()]
    available = [c for c in wanted if c in df.columns]
    if not available:
        return df
    return df[available]
