"""Factor analysis routers."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from adshare.core.logging import get_logger
from adshare.models.schemas import FactorAnalysisResponse
from adshare.services.market_data import get_market_data_service

logger = get_logger(__name__)
router = APIRouter(prefix="/factor", tags=["factor"])


@router.get("/capabilities")
async def factor_capabilities():
    """Return factor analysis capabilities."""
    return {
        "preprocessing": ["MAD去极值", "Z-Score标准化", "中位数补空"],
        "analysis": ["IC分析(Spearman)", "截面回归", "分层回测"],
        "composite": ["共线性检测", "正交化", "加权合成"],
        "sample_factors": ["ma5", "ma10", "momentum"],
    }


@router.get("/analyze", response_model=FactorAnalysisResponse)
async def analyze_factor(
    factor_name: str = Query(..., description="Factor name, e.g. ma5, momentum"),
    stock_list: str = Query(..., description="Comma-separated stock codes"),
    begin_date: int = Query(default=20240101, description="Start date YYYYMMDD"),
    end_date: int = Query(default=None, description="End date YYYYMMDD"),
    benchmark: str = Query(default="000300.SH", description="Benchmark index code"),
    group_num: int = Query(default=5, description="Number of stratification groups"),
    ic_decay: int = Query(default=20, description="IC decay period"),
):
    """Run factor analysis for given stocks.

    Not available in API-only mode — requires AmazingData SDK (worker service).
    """
    raise HTTPException(
        status_code=503,
        detail="Factor analysis requires AmazingData SDK. Use the worker service.",
    )


@router.post("/composite")
async def composite_factor(
    factor_names: List[str],
    stock_list: str,
    begin_date: int = 20240101,
    end_date: Optional[int] = None,
    weight_method: str = "equal",
    use_orthogonal: bool = True,
):
    """Composite multiple factors into a single factor.

    Not available in API-only mode — requires AmazingData SDK (worker service).
    """
    raise HTTPException(
        status_code=503,
        detail="Factor composite requires AmazingData SDK. Use the worker service.",
    )
