"""Fundamental analysis routers."""

from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from adshare.adapters.amazingdata import get_adapter
from adshare.core.logging import get_logger
from adshare.engines.fundamental.factors import (
    CATEGORY_MAP,
    DAILY_CATEGORIES,
    QUARTERLY_CATEGORIES,
    _df_to_records,
    _find_factor_category,
    _format_value,
    calc_all_factors_for_stock,
)
from adshare.models.schemas import FundamentalResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/fundamental", tags=["fundamental"])


def _get_stock_data(code: str, begin_date: Optional[int], end_date: Optional[int]):
    """Get all necessary data for fundamental analysis."""
    adapter = get_adapter()

    if end_date is None:
        from datetime import datetime
        end_date = int(datetime.now().strftime("%Y%m%d"))
    if begin_date is None:
        begin_date = 20200101

    # Get financial statements
    bs = adapter.get_financial(codes=code, statement_type="balance")
    inc = adapter.get_financial(codes=code, statement_type="income")
    cf = adapter.get_financial(codes=code, statement_type="cashflow")

    # Get K-line for daily indicators
    kline = adapter.get_kline(
        codes=code,
        begin_date=begin_date,
        end_date=end_date,
        period="day",
    )

    # Get equity structure, dividend, holder data
    # These are not yet in adapter - will be added
    equity_structure = None
    dividend = None
    holder_num = None
    share_holder = None

    return bs, inc, cf, kline, equity_structure, dividend, holder_num, share_holder


@router.get("/analyze", response_model=FundamentalResponse)
async def analyze_fundamental(
    code: str = Query(..., description="Stock code, e.g. 000001.SZ"),
    category: Optional[str] = Query(
        default=None,
        description="Category: profitability, growth, efficiency, earnings_quality, safety, governance, valuation, shareholder, size",
    ),
    factor: Optional[str] = Query(default=None, description="Specific factor name"),
    begin_date: Optional[int] = Query(default=None, description="K-line start date YYYYMMDD"),
    end_date: Optional[int] = Query(default=None, description="K-line end date YYYYMMDD"),
):
    """Run fundamental analysis for a stock."""
    try:
        bs, inc, cf, kline, equity_structure, dividend, holder_num, share_holder = _get_stock_data(
            code, begin_date, end_date
        )

        # Determine categories to calculate
        if factor:
            cat_key = _find_factor_category(factor)
            if not cat_key:
                raise HTTPException(status_code=404, detail=f"Factor {factor} not found")
            categories_to_calc = [cat_key]
        elif category and category != "all":
            if category not in CATEGORY_MAP:
                raise HTTPException(
                    status_code=400,
                    detail=f"Category {category} not found. Available: {list(CATEGORY_MAP.keys())}",
                )
            categories_to_calc = [category]
        else:
            categories_to_calc = list(CATEGORY_MAP.keys())

        q_df, d_df = calc_all_factors_for_stock(
            code, bs, inc, cf, kline,
            equity_structure, dividend, holder_num, share_holder
        )

        # Build response
        categories = {}
        for cat_key in categories_to_calc:
            cat_info = CATEGORY_MAP[cat_key]
            df = q_df if cat_key in QUARTERLY_CATEGORIES else d_df

            if df is None or df.empty:
                categories[cat_key] = {
                    "name": cat_info["name"],
                    "freq": cat_info["freq"],
                    "error": "No data available",
                }
                continue

            # Get latest values for this category's factors
            latest = {}
            for f_name in cat_info["factors"]:
                if f_name in df.columns:
                    val = df[f_name].iloc[-1]
                    latest[f_name] = _format_value(val)

            categories[cat_key] = {
                "name": cat_info["name"],
                "freq": cat_info["freq"],
                "latest_period": str(df.index[-1]),
                "latest_values": latest,
                "history": _df_to_records(df[[c for c in cat_info["factors"] if c in df.columns]], max_rows=8),
            }

        return FundamentalResponse(
            code=code,
            analysis_date=pd.Timestamp.now().strftime("%Y-%m-%d"),
            categories=categories,
            count=len(categories),
            data=categories,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Fundamental analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/factors")
async def list_factors():
    """List all available fundamental factors."""
    result = {}
    for cat_key, cat_info in CATEGORY_MAP.items():
        result[cat_key] = {
            "name": cat_info["name"],
            "freq": cat_info["freq"],
            "count": cat_info["count"],
            "factors": cat_info["factors"],
        }
    return result
