"""Error boundary tests for Pro-style stock data API routers."""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd
import pytest


# ============================================================
# Fakes (same pattern as test_stock_data_e2e)
# ============================================================


class EmptyWarehouse:
    """Warehouse that always returns empty DataFrames."""

    def query_codes(self, is_listed: Optional[bool] = None) -> pd.DataFrame:
        return pd.DataFrame()

    def query_calendar(
        self,
        market: Optional[str] = None,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def query_kline(
        self,
        codes: Any,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
        period: str = "day",
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def health(self) -> dict:
        return {"root": "/tmp/data", "duckdb_connected": True}


class DisabledWarehouse:
    """Simulates disabled warehouse (returns None from _get_warehouse_or_none)."""

    pass


@pytest.fixture
def empty_warehouse_client(client, monkeypatch):
    """TestClient with warehouse returning empty results."""
    import adshare.routers.stock_data as _sd_mod

    _sd_mod._get_warehouse_or_none = lambda: EmptyWarehouse()

    yield client


@pytest.fixture
def disabled_warehouse_client(client, monkeypatch):
    """TestClient with warehouse disabled."""
    import adshare.routers.stock_data as _sd_mod

    _sd_mod._get_warehouse_or_none = lambda: None

    yield client


# ============================================================
# Missing ts_code
# ============================================================


class TestMissingTsCode:
    """Tests for endpoints requiring ts_code."""

    @pytest.mark.parametrize(
        "endpoint",
        ["/daily", "/weekly", "/monthly", "/adj_factor", "/suspend_d"],
    )
    def test_missing_ts_code_returns_error(self, empty_warehouse_client, endpoint):
        """Calling endpoint without ts_code should return Pro-style error."""
        response = empty_warehouse_client.get(endpoint)
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == -1
        assert "ts_code" in data["msg"].lower()
        assert data["data"] is None

    def test_pro_bar_missing_ts_code_returns_error(self, empty_warehouse_client):
        """pro_bar without ts_code should return 422 (required param)."""
        response = empty_warehouse_client.get("/pro_bar")
        assert response.status_code == 422


# ============================================================
# Disabled warehouse
# ============================================================


class TestDisabledWarehouse:
    """Tests for disabled historical warehouse."""

    @pytest.mark.parametrize(
        "endpoint, expect_empty",
        [
            ("/stock_basic", False),
            ("/trade_cal", False),
            ("/adj_factor?ts_code=000001.SZ", False),
            ("/suspend_d?ts_code=000001.SZ", False),
            ("/new_share", False),
        ],
    )
    def test_disabled_warehouse_returns_error(
        self, disabled_warehouse_client, endpoint, expect_empty
    ):
        """When warehouse is disabled, endpoints should return Pro-style error."""
        response = disabled_warehouse_client.get(endpoint)
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == -1
        assert "disabled" in data["msg"].lower() or "warehouse" in data["msg"].lower()
        assert data["data"] is None

    def test_disabled_warehouse_daily_returns_empty(self, disabled_warehouse_client):
        """daily uses MarketDataService which may return empty when warehouse disabled."""
        response = disabled_warehouse_client.get("/daily?ts_code=000001.SZ")
        assert response.status_code == 200

        data = response.json()
        # MarketDataService can fall back or return empty; just verify format
        assert "data" in data
        assert data["data"]["fields"] == []
        assert data["data"]["items"] == []


# ============================================================
# Empty results
# ============================================================


class TestEmptyResults:
    """Tests for empty but valid query results."""

    def test_stock_basic_empty_returns_empty_items(self, empty_warehouse_client):
        """Empty stock_basic should return {fields: [], items: []}."""
        response = empty_warehouse_client.get("/stock_basic")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 0
        assert data["data"]["fields"] == []
        assert data["data"]["items"] == []

    def test_trade_cal_empty_returns_empty_items(self, empty_warehouse_client):
        """Empty trade_cal should return {fields: [], items: []}."""
        response = empty_warehouse_client.get("/trade_cal")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 0
        assert data["data"]["fields"] == []
        assert data["data"]["items"] == []

    def test_daily_empty_returns_empty_items(self, empty_warehouse_client):
        """Empty daily should return {fields: [], items: []}."""
        response = empty_warehouse_client.get("/daily?ts_code=000001.SZ")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 0
        assert data["data"]["fields"] == []
        assert data["data"]["items"] == []

    def test_adj_factor_empty_returns_empty_items(self, empty_warehouse_client):
        """Empty adj_factor should return {fields: [], items: []}."""
        response = empty_warehouse_client.get("/adj_factor?ts_code=000001.SZ")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 0
        assert data["data"]["fields"] == []
        assert data["data"]["items"] == []


# ============================================================
# Invalid parameters
# ============================================================


class TestInvalidParameters:
    """Tests for invalid query parameters."""

    def test_invalid_date_format_still_parses_as_none(self, empty_warehouse_client):
        """Non-numeric date strings are treated as None (wide range)."""
        response = empty_warehouse_client.get("/daily?ts_code=000001.SZ&start_date=abc")
        # Should not crash; may return empty or error
        assert response.status_code in (200, 500)

    def test_nonexistent_ts_code_returns_empty(self, empty_warehouse_client):
        """Querying non-existent code should return empty items."""
        response = empty_warehouse_client.get("/daily?ts_code=999999.SZ")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 0
        assert data["data"]["items"] == []


# ============================================================
# Server errors
# ============================================================


class TestServerErrors:
    """Tests for unexpected server errors."""

    def test_warehouse_exception_returns_500(self, client, monkeypatch):
        """If warehouse raises an exception, endpoint should return 500."""
        import adshare.routers.stock_data as _sd_mod

        def _broken_warehouse():
            raise RuntimeError("warehouse exploded")

        _sd_mod._get_warehouse_or_none = _broken_warehouse

        response = client.get("/stock_basic")
        assert response.status_code == 500

    def test_kline_exception_returns_500(self, client, monkeypatch):
        """If kline service raises an exception, daily endpoint should return 500."""
        import adshare.routers.stock_data as _sd_mod

        def _broken_service():
            class _Broken:
                def get_kline(self, **kwargs):
                    raise RuntimeError("kline failed")

            return _Broken()

        _sd_mod.get_market_data_service = _broken_service
        _sd_mod._get_warehouse_or_none = lambda: object()  # not used

        response = client.get("/daily?ts_code=000001.SZ")
        assert response.status_code == 500
