"""Fundamental analysis routers."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from adshare import dependencies as deps
from adshare.core.logging import get_logger
from adshare.models.schemas import FundamentalResponse
from adshare.services.fundamental_analysis import (
    FundamentalAnalysisError,
    FundamentalAnalysisService,
)

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
    service: FundamentalAnalysisService = Depends(deps.get_fundamental_analysis_service_dep),
):
    """Run fundamental analysis for a stock."""
    try:
        return service.analyze(
            code=code,
            category=category,
            factor=factor,
        )
    except FundamentalAnalysisError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"Fundamental analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/factors")
async def list_factors(
    service: FundamentalAnalysisService = Depends(deps.get_fundamental_analysis_service_dep),
):
    """List all available fundamental factors."""
    return service.list_categories()
