"""Factor analysis routers."""

from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from adshare.adapters.amazingdata import get_adapter
from adshare.core.cache import get_cache_manager
from adshare.core.logging import get_logger
from adshare.engines.factor.analysis import (
    build_factor_report_data,
    composite_factors,
    detect_collinearity,
    orthogonalize_factors,
    preprocess_factor,
)
from adshare.models.schemas import FactorAnalysisResponse

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


def _get_factor_data(
    factor_name: str,
    stock_list: List[str],
    begin_date: int,
    end_date: int,
):
    """Get factor data from adapter or compute from K-line."""
    adapter = get_adapter()
    cache = get_cache_manager()

    # For now, compute simple factor from K-line
    # In production, this would load pre-computed factors
    close = adapter.get_kline(
        codes=",".join(stock_list),
        begin_date=begin_date,
        end_date=end_date,
        period="day",
    )

    if close.empty:
        raise HTTPException(status_code=404, detail="No K-line data available")

    # Simple MA5 factor as example
    if factor_name.lower() == "ma5":
        factor = close.rolling(window=5, min_periods=1).mean()
    elif factor_name.lower() == "ma10":
        factor = close.rolling(window=10, min_periods=1).mean()
    elif factor_name.lower() == "momentum":
        factor = close.pct_change(20)
    else:
        # Default: return close as factor
        factor = close.copy()

    return factor


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
    """Run factor analysis for given stocks."""
    try:
        if end_date is None:
            from datetime import datetime
            end_date = int(datetime.now().strftime("%Y%m%d"))

        codes = [c.strip() for c in stock_list.split(",")]
        factor = _get_factor_data(factor_name, codes, begin_date, end_date)

        # Get close price and benchmark
        adapter = get_adapter()
        close_price = adapter.get_kline(
            codes=",".join(codes),
            begin_date=begin_date,
            end_date=end_date,
            period="day",
        )

        benchmark_df = adapter.get_kline(
            codes=benchmark,
            begin_date=begin_date,
            end_date=end_date,
            period="day",
        )
        if benchmark_df.empty:
            benchmark_df = pd.DataFrame({"close": [1.0]}, index=close_price.index[:1])
        else:
            benchmark_df = benchmark_df.to_frame(name="close") if hasattr(benchmark_df, "to_frame") else benchmark_df

        report_data = build_factor_report_data(
            factor_name=factor_name,
            factor_raw=factor,
            close_price=close_price,
            benchmark_df=benchmark_df,
            group_num=group_num,
            ic_decay=ic_decay,
        )

        # Format response
        ic_result = {}
        if report_data["ic_result"] is not None and not report_data["ic_result"].empty:
            ic_result = report_data["ic_result"].to_dict()

        group_metrics = {}
        if report_data["group_metrics"]:
            for k, v in report_data["group_metrics"].items():
                group_metrics[k] = v

        return FactorAnalysisResponse(
            factor_name=factor_name,
            stock_count=len(codes),
            begin_date=str(begin_date),
            end_date=str(end_date),
            ic_mean=ic_result.get("IC 均值", {}).get("delay_1", 0),
            ic_ir=ic_result.get("IC IR", {}).get("delay_1", 0),
            annual_return=report_data["net_analysis"].get("cumprod", {}).get("annual_return", 0),
            sharpe_ratio=report_data["net_analysis"].get("cumprod", {}).get("sharpe_ratio", 0),
            max_drawdown=report_data["net_analysis"].get("cumprod", {}).get("max_drawdown", 0),
            group_metrics=group_metrics,
            data={
                "ic_series": report_data["ic_df"].to_dict() if report_data["ic_df"] is not None else {},
                "factor_return": report_data["factor_return"].to_dict() if report_data["factor_return"] is not None else {},
            },
        )

    except HTTPException:
        raise
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
        if end_date is None:
            from datetime import datetime
            end_date = int(datetime.now().strftime("%Y%m%d"))

        codes = [c.strip() for c in stock_list.split(",")]

        # Get all factors
        factors = {}
        for name in factor_names:
            factor = _get_factor_data(name, codes, begin_date, end_date)
            factors[name] = preprocess_factor(factor)

        # Detect collinearity
        corr_matrix, vif_df, cond_num = detect_collinearity(factors)

        # Orthogonalize if needed
        need_orthogonal = use_orthogonal and (cond_num > 30 or (not vif_df.empty and any(vif_df["VIF"] > 10)))
        working_factors = orthogonalize_factors(factors) if need_orthogonal else factors

        # Composite
        if weight_method == "equal":
            weights = {name: 1.0 / len(factor_names) for name in factor_names}
        else:
            # Default equal weights
            weights = {name: 1.0 / len(factor_names) for name in factor_names}

        composite = composite_factors(working_factors, weights)

        return {
            "composite_factor": composite.to_dict(),
            "collinearity": {
                "condition_number": cond_num,
                "vif": vif_df.to_dict(),
                "correlation": corr_matrix.to_dict(),
            },
            "orthogonalized": need_orthogonal,
            "weights": weights,
        }

    except Exception as e:
        logger.error(f"Factor composite failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
