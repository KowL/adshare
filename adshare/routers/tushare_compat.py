"""Tushare Pro compatible API router.

Provides POST /dataapi/{api_name} endpoints so that tushare Pro SDK can be
pointed at adshare by setting::

    pro._DataApi__http_url = 'http://<host>:<port>/dataapi'

Supported api_name (simple version):
- daily
- weekly
- monthly
- stock_basic

Returns the same JSON shape as tushare Pro: {"code": 0, "msg": "", "data": {"fields": [...], "items": [...]}}
"""

from typing import Any, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request

from adshare.core.auth import require_auth
from adshare.core.logging import get_logger
from adshare.services.market_data import get_market_data_service

logger = get_logger(__name__)
router = APIRouter(prefix="/dataapi", tags=["tushare_compat"], dependencies=[Depends(require_auth)])


def _df_to_tushare_payload(df: pd.DataFrame) -> dict[str, Any]:
    """Convert a DataFrame to tushare Pro response data shape."""
    if df is None or df.empty:
        return {"code": 0, "msg": "", "data": {"fields": [], "items": []}}
    df = df.copy()
    # Ensure JSON-serializable types
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            df[col] = df[col].astype(object).where(pd.notna(df[col]), None)
    return {
        "code": 0,
        "msg": "",
        "data": {
            "fields": df.columns.tolist(),
            "items": df.values.tolist(),
        },
    }


@router.post("/{api_name}")
async def tushare_compat(api_name: str, request: Request):
    """Tushare Pro compatible data endpoint."""
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"tushare_compat parse body failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body") from e

    params = body.get("params") or {}
    fields = body.get("fields", "")
    token = body.get("token", "")

    logger.info(f"tushare_compat api_name={api_name} params={params} token_len={len(token)}")

    service = get_market_data_service()

    try:
        if api_name in ("daily", "weekly", "monthly"):
            period_map = {"daily": "day", "weekly": "week", "monthly": "month"}
            period = period_map[api_name]

            ts_code = params.get("ts_code", "")
            if not ts_code:
                raise HTTPException(status_code=400, detail="ts_code is required")

            start_date = str(params.get("start_date", "")).replace("-", "")
            end_date = str(params.get("end_date", "")).replace("-", "")
            if not start_date:
                start_date = "19900101"
            if not end_date:
                end_date = "20991231"

            # tushare ts_code format: 000001.SZ
            # adshare internal code format: 000001.SZ
            codes = ts_code

            limit = params.get("limit")
            offset = params.get("offset", 0)

            result = service.get_kline(
                codes=codes,
                begin_date=int(start_date),
                end_date=int(end_date),
                period=period,
                limit=int(limit) if limit is not None else None,
                offset=int(offset) if offset is not None else 0,
            )
            df = result.df

            # Map to tushare field names
            column_map = {
                "code": "ts_code",
                "date": "trade_date",
                "volume": "vol",
            }
            df = df.rename(columns=column_map)

            # Add pre_close, change, pct_chg if possible
            if "close" in df.columns:
                df["pre_close"] = df["close"].shift(-1)
                if "open" in df.columns:
                    df["change"] = df["close"] - df["pre_close"]
                    df["pct_chg"] = (df["change"] / df["pre_close"] * 100).round(4)

            # Reorder to match tushare daily convention
            preferred_cols = [
                "ts_code", "trade_date", "open", "high", "low", "close",
                "pre_close", "change", "pct_chg", "vol", "amount"
            ]
            ordered = [c for c in preferred_cols if c in df.columns]
            remaining = [c for c in df.columns if c not in preferred_cols]
            df = df[ordered + remaining]

            return _df_to_tushare_payload(df)

        elif api_name == "stock_basic":
            ts_code = params.get("ts_code", "")
            result_df = service.get_stock_basic(codes=ts_code if ts_code else None)
            if "code" in result_df.columns:
                result_df = result_df.rename(columns={"code": "ts_code"})
            return _df_to_tushare_payload(result_df)

        else:
            raise HTTPException(status_code=501, detail=f"api_name={api_name} not supported in simple version")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"tushare_compat api_name={api_name} failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
