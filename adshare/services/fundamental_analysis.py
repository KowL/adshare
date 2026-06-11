"""Application service for fundamental analysis.

Provides a clean service layer for fundamental factor calculations.
Financial data is sourced from the L3 warehouse when available,
otherwise returns a clear error indicating data is not yet populated.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from adshare.core.config import get_settings
from adshare.core.logging import get_logger
from adshare.engines.fundamental.factors import (
    CATEGORY_MAP,
    calc_earnings_quality,
    calc_efficiency,
    calc_growth,
    calc_profitability,
    calc_safety,
    calc_valuation,
)
from adshare.historical.warehouse import get_warehouse
from adshare.models.schemas import (
    FundamentalCategory,
    FundamentalResponse,
)

logger = get_logger(__name__)

# Mapping from category name to calculation function
CATEGORY_CALCULATORS = {
    "profitability": calc_profitability,
    "growth": calc_growth,
    "efficiency": calc_efficiency,
    "earnings_quality": calc_earnings_quality,
    "safety": calc_safety,
}


class FundamentalAnalysisError(Exception):
    """Domain error raised by fundamental analysis service."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class FundamentalAnalysisService:
    """Run fundamental factor calculations against financial statement data."""

    def __init__(self, warehouse=None) -> None:
        self.warehouse = warehouse

    def analyze(
        self,
        code: str,
        category: Optional[str] = None,
        factor: Optional[str] = None,
    ) -> FundamentalResponse:
        """Run fundamental analysis for a stock."""
        # Financial data is not yet stored in the L3 warehouse.
        # In the future, this will read from warehouse financial tables
        # or call the worker service via HTTP.
        raise FundamentalAnalysisError(
            503,
            "Fundamental analysis requires financial statement data. "
            "This feature will be available after the worker service "
            "populates financial data to the warehouse.",
        )

    def list_categories(self) -> dict:
        """List all available fundamental factor categories."""
        result = {}
        for cat_key, cat_info in CATEGORY_MAP.items():
            result[cat_key] = {
                "name": cat_info["name"],
                "freq": cat_info["freq"],
                "count": cat_info["count"],
                "factors": cat_info["factors"],
            }
        return result


def get_fundamental_analysis_service() -> FundamentalAnalysisService:
    """Create a fundamental analysis service for the current process."""
    return FundamentalAnalysisService()
