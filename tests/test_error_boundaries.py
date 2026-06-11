"""Error boundary tests for router-level error handling.

Tests service failure scenarios: exceptions from MarketDataService,
empty DataFrames, and malformed responses.
"""

import pandas as pd
import pytest


class TestMarketRouterErrors:
    def test_get_code_list_service_failure_returns_500(self, client, monkeypatch):
        import adshare.routers.market as _market_mod
        import adshare.services.market_data as _md_mod

        def _broken():
            raise RuntimeError("service down")

        _orig = _market_mod.get_market_data_service
        _market_mod.get_market_data_service = _broken
        try:
            response = client.get("/market/codes")
            assert response.status_code == 500
            assert "service down" in response.json()["detail"]
        finally:
            _market_mod.get_market_data_service = _orig

    def test_get_calendar_service_failure_returns_500(self, client, monkeypatch):
        import adshare.routers.market as _market_mod

        def _broken():
            raise RuntimeError("calendar error")

        _orig = _market_mod.get_market_data_service
        _market_mod.get_market_data_service = _broken
        try:
            response = client.get("/market/calendar?market=SH")
            assert response.status_code == 500
            assert "calendar error" in response.json()["detail"]
        finally:
            _market_mod.get_market_data_service = _orig

    def test_get_kline_service_failure_returns_500(self, client, monkeypatch):
        import adshare.routers.market as _market_mod

        def _broken():
            raise RuntimeError("kline error")

        _orig = _market_mod.get_market_data_service
        _market_mod.get_market_data_service = _broken
        try:
            response = client.get(
                "/market/kline?codes=000001.SZ&begin_date=20240101&end_date=20241231"
            )
            assert response.status_code == 500
            assert "kline error" in response.json()["detail"]
        finally:
            _market_mod.get_market_data_service = _orig

    def test_get_snapshot_service_failure_returns_500(self, client, monkeypatch):
        import adshare.routers.market as _market_mod

        def _broken():
            raise RuntimeError("snapshot error")

        _orig = _market_mod.get_market_data_service
        _market_mod.get_market_data_service = _broken
        try:
            response = client.get("/market/snapshot?codes=000001.SZ")
            assert response.status_code == 500
            assert "snapshot error" in response.json()["detail"]
        finally:
            _market_mod.get_market_data_service = _orig

    def test_get_stock_basic_service_failure_returns_500(self, client, monkeypatch):
        import adshare.routers.market as _market_mod

        def _broken():
            raise RuntimeError("stock basic error")

        _orig = _market_mod.get_market_data_service
        _market_mod.get_market_data_service = _broken
        try:
            response = client.get("/market/stock/basic")
            assert response.status_code == 500
            assert "stock basic error" in response.json()["detail"]
        finally:
            _market_mod.get_market_data_service = _orig


class TestLimitUpRouterErrors:
    def test_limit_up_service_failure_returns_500(self, client, monkeypatch):
        import adshare.routers.market as _market_mod
        import adshare.services.limit_up as _lu_mod

        def _broken():
            raise RuntimeError("limit-up error")

        _orig = _market_mod.get_limit_up_service
        _market_mod.get_limit_up_service = _broken
        try:
            response = client.get("/market/limit-up")
            assert response.status_code == 500
            assert "limit-up error" in response.json()["detail"]
        finally:
            _market_mod.get_limit_up_service = _orig

    def test_limit_down_service_failure_returns_500(self, client, monkeypatch):
        import adshare.routers.market as _market_mod
        import adshare.services.limit_up as _lu_mod

        def _broken():
            raise RuntimeError("limit-down error")

        _orig = _market_mod.get_limit_down_service
        _market_mod.get_limit_down_service = _broken
        try:
            response = client.get("/market/limit-down")
            assert response.status_code == 500
            assert "limit-down error" in response.json()["detail"]
        finally:
            _market_mod.get_limit_down_service = _orig

    def test_market_activity_service_failure_returns_500(self, client, monkeypatch):
        import adshare.routers.market as _market_mod

        def _broken():
            raise RuntimeError("market activity error")

        _orig = _market_mod.get_market_activity_service
        _market_mod.get_market_activity_service = _broken
        try:
            response = client.get("/market/market-activity")
            assert response.status_code == 500
            assert "market activity error" in response.json()["detail"]
        finally:
            _market_mod.get_market_activity_service = _orig

    def test_strong_pool_service_failure_returns_500(self, client, monkeypatch):
        import adshare.routers.market as _market_mod

        def _broken():
            raise RuntimeError("strong pool error")

        _orig = _market_mod.get_strong_stock_pool_service
        _market_mod.get_strong_stock_pool_service = _broken
        try:
            response = client.get("/market/strong-pool")
            assert response.status_code == 500
            assert "strong pool error" in response.json()["detail"]
        finally:
            _market_mod.get_strong_stock_pool_service = _orig


class TestTechnicalRouterErrors:
    def test_technical_analyze_service_failure_returns_500(self, client, monkeypatch):
        import adshare.routers.technical as _tech_mod
        import adshare.services.technical_analysis as _ta_mod

        class BrokenService:
            def analyze(self, **kwargs):
                raise RuntimeError("technical analysis error")

        _orig = _tech_mod.get_technical_analysis_service
        _tech_mod.get_technical_analysis_service = lambda: BrokenService()
        try:
            response = client.get("/technical/analyze?code=000001.SZ")
            assert response.status_code == 500
            assert "technical analysis error" in response.json()["detail"]
        finally:
            _tech_mod.get_technical_analysis_service = _orig


class TestEmptyDataHandling:
    def test_kline_empty_df_returns_zero_count(self, client, monkeypatch):
        """When service returns empty DataFrame, response should have count=0."""
        import adshare.routers.market as _market_mod
        import adshare.services.market_data as _md_mod

        class EmptyService:
            def get_kline(self, **kwargs):
                return _md_mod.KlineQueryResult(df=pd.DataFrame(), source="warehouse", synced=True)

        _orig = _market_mod.get_market_data_service
        _market_mod.get_market_data_service = lambda: EmptyService()
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
            _market_mod.get_market_data_service = _orig

    def test_snapshot_empty_df_returns_zero_count(self, client, monkeypatch):
        import adshare.routers.market as _market_mod

        class EmptyService:
            def get_snapshot(self, **kwargs):
                return pd.DataFrame()

        _orig = _market_mod.get_market_data_service
        _market_mod.get_market_data_service = lambda: EmptyService()
        try:
            response = client.get("/market/snapshot?codes=UNKNOWN.CODE")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 0
            assert data["data"] == []
        finally:
            _market_mod.get_market_data_service = _orig

    def test_stock_basic_empty_df_returns_zero_count(self, client, monkeypatch):
        import adshare.routers.market as _market_mod

        class EmptyService:
            def get_stock_basic(self, **kwargs):
                return pd.DataFrame()

        _orig = _market_mod.get_market_data_service
        _market_mod.get_market_data_service = lambda: EmptyService()
        try:
            response = client.get("/market/stock/basic?codes=UNKNOWN.CODE")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 0
            assert data["data"] == []
        finally:
            _market_mod.get_market_data_service = _orig
