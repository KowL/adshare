"""FastAPI dependency providers for adshare services.

Centralises the wiring between global factories and FastAPI's ``Depends``
mechanism. Tests can monkeypatch these provider functions to inject fakes.
"""

from __future__ import annotations

from typing import Optional

from adshare.core.cache import CacheManager, get_cache_manager
from adshare.core.config import Settings, get_settings
from adshare.historical.warehouse import HistoricalWarehouse, get_warehouse
from adshare.services.factor_analysis import FactorAnalysisService, get_factor_analysis_service
from adshare.services.fundamental_analysis import (
    FundamentalAnalysisService,
    get_fundamental_analysis_service,
)
from adshare.services.limit_up import (
    LimitDownService,
    LimitUpService,
    MarketActivityService,
    StrongStockPoolService,
    get_limit_down_service,
    get_limit_up_service,
    get_market_activity_service,
    get_strong_stock_pool_service,
)
from adshare.services.market_data import MarketDataService, get_market_data_service
from adshare.services.realtime_broadcast import (
    RealtimeBroadcastService,
    get_broadcast_service,
)
from adshare.services.technical_analysis import (
    TechnicalAnalysisService,
    get_technical_analysis_service,
)


# ---------------------------------------------------------------------------
# Settings / infrastructure
# ---------------------------------------------------------------------------


def get_settings_dep() -> Settings:
    """Provide application settings."""
    return get_settings()


def get_cache_manager_dep() -> CacheManager:
    """Provide the Redis cache manager."""
    return get_cache_manager()


def get_warehouse_dep() -> Optional[HistoricalWarehouse]:
    """Provide the L3 historical warehouse, or None if disabled."""
    settings = get_settings()
    if not settings.historical_enabled:
        return None
    return get_warehouse(settings)


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


def get_market_data_service_dep() -> MarketDataService:
    """Provide the market data service."""
    return get_market_data_service()


# ---------------------------------------------------------------------------
# Limit-up / limit-down
# ---------------------------------------------------------------------------


def get_limit_up_service_dep() -> LimitUpService:
    """Provide the limit-up service."""
    return get_limit_up_service()


def get_limit_down_service_dep() -> LimitDownService:
    """Provide the limit-down service."""
    return get_limit_down_service()


def get_market_activity_service_dep() -> MarketActivityService:
    """Provide the market activity service."""
    return get_market_activity_service()


def get_strong_stock_pool_service_dep() -> StrongStockPoolService:
    """Provide the strong stock pool service."""
    return get_strong_stock_pool_service()


# ---------------------------------------------------------------------------
# Analysis services
# ---------------------------------------------------------------------------


def get_technical_analysis_service_dep() -> TechnicalAnalysisService:
    """Provide the technical analysis service."""
    return get_technical_analysis_service()


def get_fundamental_analysis_service_dep() -> FundamentalAnalysisService:
    """Provide the fundamental analysis service."""
    return get_fundamental_analysis_service()


def get_factor_analysis_service_dep() -> FactorAnalysisService:
    """Provide the factor analysis service."""
    return get_factor_analysis_service()


# ---------------------------------------------------------------------------
# Realtime
# ---------------------------------------------------------------------------


def get_broadcast_service_dep() -> RealtimeBroadcastService:
    """Provide the realtime broadcast service."""
    return get_broadcast_service()
