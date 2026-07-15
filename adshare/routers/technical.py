"""Technical analysis routers."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from adshare import dependencies as deps
from adshare.core.logging import get_logger
from adshare.engines.technical.indicators import CATEGORY_MAP
from adshare.models.schemas import TechnicalResponse
from adshare.services.technical_analysis import (
    TechnicalAnalysisError,
    TechnicalAnalysisService,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/technical", tags=["technical"])


@router.get("/analyze", response_model=TechnicalResponse)
async def analyze_technical(
    code: str = Query(..., description="Stock code, e.g. 000001.SZ"),
    begin_date: Optional[int] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[int] = Query(default=None, description="End date YYYYMMDD"),
    indicator: Optional[str] = Query(default=None, description="Specific indicator name"),
    category: Optional[str] = Query(
        default=None,
        description="Category: overbought_oversold, trend, energy, volume, ma, path, other",
    ),
    service: TechnicalAnalysisService = Depends(deps.get_technical_analysis_service_dep),
):
    """Run technical analysis for a stock."""
    try:
        return service.analyze(
            code=code,
            begin_date=begin_date,
            end_date=end_date,
            indicator=indicator,
            category=category,
        )
    except TechnicalAnalysisError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"Technical analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/indicators")
async def list_indicators():
    """List all available technical indicators."""
    result = {}
    for cat_key, indicators in CATEGORY_MAP.items():
        result[cat_key] = [name for name, _ in indicators]
    return result
