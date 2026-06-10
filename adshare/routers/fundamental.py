"""Fundamental analysis routers."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from adshare.core.logging import get_logger
from adshare.engines.fundamental.factors import CATEGORY_MAP
from adshare.models.schemas import FundamentalResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/fundamental", tags=["fundamental"])


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
    """Run fundamental analysis for a stock.

    Not available in API-only mode — requires AmazingData SDK (worker service).
    """
    raise HTTPException(
        status_code=503,
        detail="Fundamental analysis requires AmazingData SDK. Use the worker service.",
    )


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
