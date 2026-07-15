"""Error boundary tests for router-level error handling.

Tests service failure scenarios: exceptions from MarketDataService,
empty DataFrames, and malformed responses.
"""

import pandas as pd
import pytest


class TestMarketRouterErrors:
    def test_get_code_list_service_failure_returns_500(self, client, monkeypatch):
        from adshare import dependencies as deps
        from adshare.services.market_data import MarketDataService

        class BrokenService(MarketDataService):
            def get_code_list(self, security_type="EXTRA_STOCK_A"):
                raise RuntimeError("service down")

        client.app.dependency_overrides[deps.get_market_data_service_dep] = lambda: BrokenService()
        try:
            response = client.get("/market/codes")
            assert response.status_code == 500
            assert "service down" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.pop(deps.get_market_data_service_dep, None)

    def test_get_calendar_service_failure_returns_500(self, client, monkeypatch):
        from adshare import dependencies as deps
        from adshare.services.market_data import MarketDataService

        class BrokenService(MarketDataService):
            def get_calendar(self, market="SH", date=None):
                raise RuntimeError("calendar error")

        client.app.dependency_overrides[deps.get_market_data_service_dep] = lambda: BrokenService()
        try:
            response = client.get("/market/calendar?market=SH")
            assert response.status_code == 500
            assert "calendar error" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.pop(deps.get_market_data_service_dep, None)

    def test_get_kline_service_failure_returns_500(self, client, monkeypatch):
        from adshare import dependencies as deps
        from adshare.services.market_data import MarketDataService

        class BrokenService(MarketDataService):
            def get_kline(self, **kwargs):
                raise RuntimeError("kline error")

        client.app.dependency_overrides[deps.get_market_data_service_dep] = lambda: BrokenService()
        try:
            response = client.get(
                "/market/kline?codes=000001.SZ&begin_date=20240101&end_date=20241231"
            )
            assert response.status_code == 500
            assert "kline error" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.pop(deps.get_market_data_service_dep, None)

    def test_get_snapshot_service_failure_returns_500(self, client, monkeypatch):
        from adshare import dependencies as deps
        from adshare.services.market_data import MarketDataService

        class BrokenService(MarketDataService):
            def get_snapshot(self, **kwargs):
                raise RuntimeError("snapshot error")

        client.app.dependency_overrides[deps.get_market_data_service_dep] = lambda: BrokenService()
        try:
            response = client.get("/market/snapshot?codes=000001.SZ")
            assert response.status_code == 500
            assert "snapshot error" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.pop(deps.get_market_data_service_dep, None)

    def test_get_stock_basic_service_failure_returns_500(self, client, monkeypatch):
        from adshare import dependencies as deps
        from adshare.services.market_data import MarketDataService

        class BrokenService(MarketDataService):
            def get_stock_basic(self, **kwargs):
                raise RuntimeError("stock basic error")

        client.app.dependency_overrides[deps.get_market_data_service_dep] = lambda: BrokenService()
        try:
            response = client.get("/market/stock/basic")
            assert response.status_code == 500
            assert "stock basic error" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.pop(deps.get_market_data_service_dep, None)


class TestLimitUpRouterErrors:
    def test_limit_up_service_failure_returns_500(self, client, monkeypatch):
        from adshare import dependencies as deps
        from adshare.services.limit_up import LimitUpService

        class BrokenService(LimitUpService):
            def get_limit_up(self, **kwargs):
                raise RuntimeError("limit-up error")

        client.app.dependency_overrides[deps.get_limit_up_service_dep] = lambda: BrokenService()
        try:
            response = client.get("/market/limit-up")
            assert response.status_code == 500
            assert "limit-up error" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.pop(deps.get_limit_up_service_dep, None)

    def test_limit_down_service_failure_returns_500(self, client, monkeypatch):
        from adshare import dependencies as deps
        from adshare.services.limit_up import LimitDownService

        class BrokenService(LimitDownService):
            def get_limit_down(self, **kwargs):
                raise RuntimeError("limit-down error")

        client.app.dependency_overrides[deps.get_limit_down_service_dep] = lambda: BrokenService()
        try:
            response = client.get("/market/limit-down")
            assert response.status_code == 500
            assert "limit-down error" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.pop(deps.get_limit_down_service_dep, None)

    def test_market_activity_service_failure_returns_500(self, client, monkeypatch):
        from adshare import dependencies as deps
        from adshare.services.limit_up import MarketActivityService

        class BrokenService(MarketActivityService):
            def get_market_activity(self, **kwargs):
                raise RuntimeError("market activity error")

        client.app.dependency_overrides[deps.get_market_activity_service_dep] = lambda: BrokenService()
        try:
            response = client.get("/market/market-activity")
            assert response.status_code == 500
            assert "market activity error" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.pop(deps.get_market_activity_service_dep, None)

    def test_strong_pool_service_failure_returns_500(self, client, monkeypatch):
        from adshare import dependencies as deps
        from adshare.services.limit_up import StrongStockPoolService

        class BrokenService(StrongStockPoolService):
            def get_strong_pool(self, **kwargs):
                raise RuntimeError("strong pool error")

        client.app.dependency_overrides[deps.get_strong_stock_pool_service_dep] = lambda: BrokenService()
        try:
            response = client.get("/market/strong-pool")
            assert response.status_code == 500
            assert "strong pool error" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.pop(deps.get_strong_stock_pool_service_dep, None)


class TestTechnicalRouterErrors:
    def test_technical_analyze_service_failure_returns_500(self, client, monkeypatch):
        from adshare import dependencies as deps
        from adshare.services.technical_analysis import TechnicalAnalysisService

        class BrokenService(TechnicalAnalysisService):
            def analyze(self, **kwargs):
                raise RuntimeError("technical analysis error")

        client.app.dependency_overrides[deps.get_technical_analysis_service_dep] = lambda: BrokenService()
        try:
            response = client.get("/technical/analyze?code=000001.SZ")
            assert response.status_code == 500
            assert "technical analysis error" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.pop(deps.get_technical_analysis_service_dep, None)


class TestEmptyDataHandling:
    def test_kline_empty_df_returns_zero_count(self, client, monkeypatch):
        """When service returns empty DataFrame, response should have count=0."""
        from adshare import dependencies as deps
        from adshare.services.market_data import KlineQueryResult, MarketDataService

        class EmptyService(MarketDataService):
            def get_kline(self, **kwargs):
                return KlineQueryResult(df=pd.DataFrame(), source="warehouse", synced=True)

        client.app.dependency_overrides[deps.get_market_data_service_dep] = lambda: EmptyService()
        try:
            response = client.get(
                "/market/kline?codes=UNKNOWN.CODE&begin_date=20240101&end_date=20241231"
            )
            # Router catches exceptions and returns 500; empty df from service itself is fine
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 0
            assert data["data"] == []
        finally:
            client.app.dependency_overrides.pop(deps.get_market_data_service_dep, None)

    def test_snapshot_empty_df_returns_zero_count(self, client, monkeypatch):
        from adshare import dependencies as deps
        from adshare.services.market_data import MarketDataService

        class EmptyService(MarketDataService):
            def get_snapshot(self, **kwargs):
                return pd.DataFrame()

        client.app.dependency_overrides[deps.get_market_data_service_dep] = lambda: EmptyService()
        try:
            response = client.get("/market/snapshot?codes=UNKNOWN.CODE")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 0
            assert data["data"] == []
        finally:
            client.app.dependency_overrides.pop(deps.get_market_data_service_dep, None)

    def test_stock_basic_empty_df_returns_zero_count(self, client, monkeypatch):
        from adshare import dependencies as deps
        from adshare.services.market_data import MarketDataService

        class EmptyService(MarketDataService):
            def get_stock_basic(self, **kwargs):
                return pd.DataFrame()

        client.app.dependency_overrides[deps.get_market_data_service_dep] = lambda: EmptyService()
        try:
            response = client.get("/market/stock/basic?codes=UNKNOWN.CODE")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 0
            assert data["data"] == []
        finally:
            client.app.dependency_overrides.pop(deps.get_market_data_service_dep, None)
