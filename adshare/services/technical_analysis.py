"""Application service for technical analysis."""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Optional

import pandas as pd

from adshare.core.logging import get_logger
from adshare.core.exceptions import ServiceError
from adshare.engines.technical.indicators import CATEGORY_MAP, get_indicator
from adshare.models.schemas import TechnicalResponse
from adshare.services.market_data import MarketDataService, get_market_data_service

logger = get_logger(__name__)


class TechnicalAnalysisError(ServiceError):
    """Domain error raised by technical analysis service."""


class TechnicalAnalysisService:
    """Run technical indicators against standardized K-line inputs."""

    def __init__(self, market_data_service: Optional[MarketDataService] = None) -> None:
        self.market_data_service = market_data_service

    def analyze(
        self,
        code: str,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
        indicator: Optional[str] = None,
        category: Optional[str] = None,
    ) -> TechnicalResponse:
        """Run technical analysis for a stock."""
        begin_date, end_date = _resolve_date_range(begin_date, end_date)
        df = self._get_kline_data(code, begin_date, end_date)
        if df.empty:
            raise TechnicalAnalysisError(404, f"No K-line data for {code}")

        close = df["close"]
        open_ = df["open"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        amount = df.get("amount", close * volume)

        price_info = {
            "open": round(float(open_.iloc[-1]), 2),
            "high": round(float(high.iloc[-1]), 2),
            "low": round(float(low.iloc[-1]), 2),
            "close": round(float(close.iloc[-1]), 2),
            "volume": int(volume.iloc[-1]),
            "amount": round(float(amount.iloc[-1]), 2),
        }

        if indicator:
            categories = self._analyze_single_indicator(indicator, close, high, low, volume, open_, amount)
        else:
            categories = self._analyze_categories(category, close, high, low, volume, open_, amount)

        return TechnicalResponse(
            code=code,
            date=_latest_date(df),
            price=price_info,
            categories=categories,
            count=len(categories),
            data=categories,
        )

    def _get_kline_data(self, code: str, begin_date: int, end_date: int) -> pd.DataFrame:
        service = self._get_market_data_service()
        result = service.get_kline(
            codes=code,
            begin_date=begin_date,
            end_date=end_date,
            period="day",
            source="auto",
        )
        return result.df

    def _analyze_single_indicator(
        self,
        indicator: str,
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        volume: pd.Series,
        open_: pd.Series,
        amount: pd.Series,
    ) -> dict:
        ind_func = get_indicator(indicator)
        if ind_func is None:
            raise TechnicalAnalysisError(404, f"Indicator {indicator} not found")

        try:
            params = _get_indicator_params(ind_func, close, high, low, volume, open_, amount)
            result = ind_func(**params)
            values = {key: _format_value(value.iloc[-1]) for key, value in result.items()}
            return {
                "indicator": {
                    "name": indicator.upper(),
                    "indicators": [
                        {
                            "name": indicator.upper(),
                            "values": values,
                        }
                    ],
                }
            }
        except Exception as e:
            logger.error("Indicator %s calculation failed: %s", indicator, e)
            raise TechnicalAnalysisError(500, str(e)) from e

    def _analyze_categories(
        self,
        category: Optional[str],
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        volume: pd.Series,
        open_: pd.Series,
        amount: pd.Series,
    ) -> dict:
        if category:
            if category not in CATEGORY_MAP:
                raise TechnicalAnalysisError(
                    400,
                    f"Category {category} not found. Available: {list(CATEGORY_MAP.keys())}",
                )
            categories_to_calc = {category: CATEGORY_MAP[category]}
        else:
            categories_to_calc = CATEGORY_MAP

        categories = {}
        for cat_key, indicators in categories_to_calc.items():
            cat_results = {}
            for ind_name, ind_func in indicators:
                try:
                    params = _get_indicator_params(ind_func, close, high, low, volume, open_, amount)
                    result = ind_func(**params)
                    values = {key: _format_value(value.iloc[-1]) for key, value in result.items()}
                    cat_results[ind_name] = values
                except Exception as e:  # noqa: BLE001
                    logger.warning("Indicator %s failed: %s", ind_name, e)
                    continue

            categories[cat_key] = {
                "name": cat_key,
                "indicators": [{"name": name, "values": values} for name, values in cat_results.items()],
            }

        return categories

    def _get_market_data_service(self) -> MarketDataService:
        if self.market_data_service is None:
            self.market_data_service = get_market_data_service()
        return self.market_data_service


def get_technical_analysis_service() -> TechnicalAnalysisService:
    """Create a technical analysis service for the current process."""
    return TechnicalAnalysisService()


def _resolve_date_range(begin_date: Optional[int], end_date: Optional[int]) -> tuple[int, int]:
    if end_date is None:
        end_date = int(datetime.now().strftime("%Y%m%d"))
    if begin_date is None:
        begin_date = 20240101
    return begin_date, end_date


def _latest_date(df: pd.DataFrame) -> str:
    if "kline_time" in df.columns:
        return str(df["kline_time"].iloc[-1])
    return str(df.index[-1]) if hasattr(df, "index") else "N/A"


def _format_value(value):
    """Format numeric value with appropriate precision."""
    if pd.isna(value):
        return None
    if isinstance(value, int):
        return int(value)
    abs_val = abs(float(value))
    if abs_val >= 100:
        return round(float(value), 2)
    if abs_val >= 10:
        return round(float(value), 3)
    if abs_val >= 1:
        return round(float(value), 4)
    return round(float(value), 6)


def _get_indicator_params(ind_func, close, high, low, volume, open_, amount) -> dict:
    """Build parameter dict based on indicator function signature."""
    sig = inspect.signature(ind_func)
    params = {}
    param_names = list(sig.parameters.keys())

    if "close" in param_names:
        params["close"] = close
    if "high" in param_names:
        params["high"] = high
    if "low" in param_names:
        params["low"] = low
    if "volume" in param_names:
        params["volume"] = volume
    if "open_" in param_names:
        params["open_"] = open_
    if "amount" in param_names:
        params["amount"] = amount

    return params
