"""DataFrame formatter for Pro-style API responses.

Converts pandas DataFrames to the {fields, items} response format
used by the Pro data platform APIs.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import pandas as pd


def to_fields_items(
    df: pd.DataFrame,
    field_map: Optional[Dict[str, str]] = None,
    converters: Optional[Dict[str, Callable]] = None,
) -> Dict[str, Any]:
    """Convert a DataFrame to {"fields": [...], "items": [[...], ...]} format.

    Args:
        df: Input DataFrame.
        field_map: Optional column rename mapping {old_name: new_name}.
        converters: Optional column value converters {col_name: callable}.

    Returns:
        Dictionary with "fields" and "items" keys.
    """
    if df is None or df.empty:
        return {"fields": [], "items": []}

    df = df.copy()

    if field_map:
        df = df.rename(columns={k: v for k, v in field_map.items() if k in df.columns})

    if converters:
        for col, fn in converters.items():
            if col in df.columns:
                df[col] = df[col].apply(fn)

    # Ensure all values are JSON-friendly
    fields = list(df.columns)
    items = []
    for _, row in df.iterrows():
        items.append([_jsonify(v) for v in row.tolist()])

    return {"fields": fields, "items": items}


def build_response(
    data: Optional[Dict[str, Any]] = None,
    code: int = 0,
    msg: str = "success",
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a standard Pro-style API response.

    Args:
        data: The data payload (typically from to_fields_items).
        code: Response code (0 = success).
        msg: Response message.
        request_id: Optional request tracking ID.

    Returns:
        Standard response dictionary.
    """
    return {
        "code": code,
        "msg": msg,
        "data": data,
        "request_id": request_id,
    }


def build_error_response(
    msg: str,
    code: int = -1,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a standard error response."""
    return build_response(data=None, code=code, msg=msg, request_id=request_id)


def _jsonify(value: Any) -> Any:
    """Make a single value JSON-friendly."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    import numpy as np
    from datetime import datetime

    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value) if not np.isnan(value) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y%m%d") if hasattr(value, "strftime") else str(value)
    return str(value)
