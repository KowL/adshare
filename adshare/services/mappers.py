"""Response mappers shared by routers and services."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List

import numpy as np
import pandas as pd

from adshare.models.schemas import HistoricalKlineRecord, KlineItem, SnapshotItem


def dataframe_to_kline_items(df: pd.DataFrame) -> List[KlineItem]:
    """Convert a K-line DataFrame to public market K-line response items."""
    items: List[KlineItem] = []
    if df is None or df.empty:
        return items

    for _, row in df.iterrows():
        date_val = row.get("date") or row.get("kline_time") or 0
        if hasattr(date_val, "strftime"):
            date_val = int(date_val.strftime("%Y%m%d"))
        else:
            date_val = int(date_val) if date_val else 0

        items.append(
            KlineItem(
                code=str(row.get("code", "")),
                date=date_val,
                open=float(row.get("open", 0)),
                high=float(row.get("high", 0)),
                low=float(row.get("low", 0)),
                close=float(row.get("close", 0)),
                volume=int(row.get("volume", 0)),
                amount=float(row.get("amount", 0)),
            )
        )
    return items


def dataframe_to_historical_kline_records(df: pd.DataFrame) -> List[HistoricalKlineRecord]:
    """Convert a K-line DataFrame to L3 historical response records."""
    records: List[HistoricalKlineRecord] = []
    if df is None or df.empty:
        return records

    for _, row in df.iterrows():
        records.append(
            HistoricalKlineRecord(
                code=str(row.get("code", "")),
                date=int(row.get("date", 0) or 0),
                open=float(row.get("open", 0) or 0.0),
                high=float(row.get("high", 0) or 0.0),
                low=float(row.get("low", 0) or 0.0),
                close=float(row.get("close", 0) or 0.0),
                volume=int(row.get("volume", 0) or 0),
                amount=float(row.get("amount", 0) or 0.0),
                adj_factor=(
                    float(row.get("adj_factor"))
                    if "adj_factor" in row and pd.notna(row.get("adj_factor"))
                    else None
                ),
                is_suspended=(
                    bool(row.get("is_suspended"))
                    if "is_suspended" in row and pd.notna(row.get("is_suspended"))
                    else None
                ),
                sync_at=(
                    int(row.get("sync_at"))
                    if "sync_at" in row and pd.notna(row.get("sync_at"))
                    else None
                ),
            )
        )
    return records


def dataframe_to_snapshot_items(df: pd.DataFrame) -> List[SnapshotItem]:
    """Convert a snapshot DataFrame to public snapshot response items."""
    items: List[SnapshotItem] = []
    if df is None or df.empty:
        return items

    for _, row in df.iterrows():
        items.append(
            SnapshotItem(
                code=str(row.get("code", "")),
                date=int(row.get("date", 0)),
                time=int(row.get("time", 0)) if "time" in row else None,
                open=float(row.get("open")) if "open" in row else None,
                high=float(row.get("high")) if "high" in row else None,
                low=float(row.get("low")) if "low" in row else None,
                close=float(row.get("close")) if "close" in row else None,
                volume=int(row.get("volume")) if "volume" in row else None,
                amount=float(row.get("amount")) if "amount" in row else None,
            )
        )
    return items


def dataframe_to_json_rows(df: pd.DataFrame) -> List[List[Any]]:
    """Convert a DataFrame to JSON-friendly row lists."""
    rows: List[List[Any]] = []
    if df is None or df.empty:
        return rows
    for _, row in df.iterrows():
        rows.append([jsonify_value(v) for v in row.tolist()])
    return rows


def jsonify_value(value: Any) -> Any:
    """Make pandas/numpy values JSON-friendly."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (pd.Timestamp, datetime)):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return str(value)
