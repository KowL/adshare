"""Factor analysis routers."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from adshare.core.logging import get_logger
from adshare.models.schemas import FactorAnalysisResponse
from adshare.services.factor_analysis import (
    FactorAnalysisError,
    get_factor_analysis_service,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/factor", tags=["factor"])


@router.get("/capabilities")
async def factor_capabilities():
    """Return factor analysis capabilities."""
    service = get_factor_analysis_service()
    return service.capabilities()


@router.get("/analyze", response_model=FactorAnalysisResponse)
async def analyze_factor(
    factor_name: str = Query(..., description="Factor name, e.g. ma5, momentum"),
    stock_list: str = Query(..., description="Comma-separated stock codes"),
    begin_date: int = Query(default=20240101, description="Start date YYYYMMDD"),
    end_date: Optional[int] = Query(default=None, description="End date YYYYMMDD"),
    benchmark: str = Query(default="000300.SH", description="Benchmark index code"),
    group_num: int = Query(default=5, description="Number of stratification groups"),
    ic_decay: int = Query(default=20, description="IC decay period"),
):
    """Run factor analysis for given stocks."""
    try:
        service = get_factor_analysis_service()
        return service.analyze(
            factor_name=factor_name,
            stock_list=[s.strip() for s in stock_list.split(",") if s.strip()],
            begin_date=begin_date,
            end_date=end_date,
            benchmark=benchmark,
            group_num=group_num,
            ic_decay=ic_decay,
        )
    except FactorAnalysisError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"Factor analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/composite")
async def composite_factor(
    factor_names: List[str],
    stock_list: str,
    begin_date: int = 20240101,
    end_date: Optional[int] = None,
    weight_method: str = "equal",
    use_orthogonal: bool = True,
):
    """Composite multiple factors into a single factor."""
    try:
        service = get_factor_analysis_service()
        return service.composite(
            factor_names=factor_names,
            stock_list=stock_list,
            begin_date=begin_date,
            end_date=end_date,
            weight_method=weight_method,
            use_orthogonal=use_orthogonal,
        )
    except FactorAnalysisError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"Factor composite failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
