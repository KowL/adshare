"""Application service for factor analysis.

Provides a clean service layer for factor IC analysis, regression,
stratification backtest, and composite factor building.
"""

from __future__ import annotations

from typing import List, Optional

from adshare.core.logging import get_logger
from adshare.models.schemas import FactorAnalysisResponse

logger = get_logger(__name__)


class FactorAnalysisError(Exception):
    """Domain error raised by factor analysis service."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class FactorAnalysisService:
    """Run factor analysis (IC, regression, stratification) against K-line data."""

    def analyze(
        self,
        factor_name: str,
        stock_list: List[str],
        begin_date: int,
        end_date: Optional[int],
        benchmark: str,
        group_num: int,
        ic_decay: int,
    ) -> FactorAnalysisResponse:
        """Run factor analysis for given stocks.

        Factor data is computed from K-line data in the L3 warehouse.
        In the future, pre-computed factor tables may be available.
        """
        # Factor analysis requires cross-sectional factor values,
        # which are not yet pre-computed in the warehouse.
        raise FactorAnalysisError(
            503,
            "Factor analysis requires pre-computed factor data. "
            "This feature will be available after the worker service "
            "populates factor tables to the warehouse.",
        )

    def composite(
        self,
        factor_names: List[str],
        stock_list: str,
        begin_date: int,
        end_date: Optional[int],
        weight_method: str,
        use_orthogonal: bool,
    ) -> dict:
        """Composite multiple factors into a single factor."""
        raise FactorAnalysisError(
            503,
            "Factor composite requires pre-computed factor data. "
            "This feature will be available after the worker service "
            "populates factor tables to the warehouse.",
        )

    def capabilities(self) -> dict:
        """Return factor analysis capabilities."""
        return {
            "preprocessing": ["MAD去极值", "Z-Score标准化", "中位数补空"],
            "analysis": ["IC分析(Spearman)", "截面回归", "分层回测"],
            "composite": ["共线性检测", "正交化", "加权合成"],
            "sample_factors": ["ma5", "ma10", "momentum"],
        }


def get_factor_analysis_service() -> FactorAnalysisService:
    """Create a factor analysis service for the current process."""
    return FactorAnalysisService()
