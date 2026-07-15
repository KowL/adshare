"""End-to-end tests for Pro-style stock data API routers.

Uses TestClient to verify HTTP endpoints return the expected Pro platform
response format ({code, msg, data: {fields, items}, request_id}).
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional

import pandas as pd
import pytest

from adshare import dependencies as deps


# ============================================================
# Fakes
# ============================================================


class FakeWarehouse:
    """Fake historical warehouse for stock_data e2e tests."""

    def __init__(self) -> None:
        self._codes_df = pd.DataFrame(
            {
                "code": ["000001.SZ", "600519.SH", "300001.SZ"],
                "name": ["平安银行", "贵州茅台", "特锐德"],
                "comp_name": [
                    "平安银行股份有限公司",
                    "贵州茅台酒股份有限公司",
                    "青岛特锐德电气股份有限公司",
                ],
                "list_date": [19910403, 20010827, 20091030],
                "delist_date": [None, None, None],
                "list_plate": ["主板", "主板", "创业板"],
                "is_listed": [1, 1, 1],
                "board": ["主板", "主板", "创业板"],
            }
        )
        self._calendar_df = pd.DataFrame(
            {
                "date": [20260608, 20260609, 20260610, 20260611, 20260612],
                "is_open": [1, 1, 1, 1, 1],
            }
        )
        self._kline_df = pd.DataFrame(
            {
                "code": ["000001.SZ"] * 5,
                "date": [20260608, 20260609, 20260610, 20260611, 20260612],
                "open": [10.0, 10.1, 10.2, 10.3, 10.4],
                "high": [10.2, 10.3, 10.4, 10.5, 10.6],
                "low": [9.9, 10.0, 10.1, 10.2, 10.3],
                "close": [10.1, 10.2, 10.3, 10.4, 10.5],
                "volume": [10000, 11000, 12000, 13000, 14000],
                "amount": [100000.0, 110000.0, 120000.0, 130000.0, 140000.0],
                "adj_factor": [1.0, 1.01, 1.02, 1.03, 1.04],
            }
        )

    def query_codes(self, is_listed: Optional[bool] = None) -> pd.DataFrame:
        df = self._codes_df.copy()
        if is_listed is not None:
            want = 1 if is_listed else 0
            df = df[df["is_listed"] == want]
        return df

    def query_calendar(
        self,
        market: Optional[str] = None,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        df = self._calendar_df.copy()
        if begin_date is not None:
            df = df[df["date"] >= begin_date]
        if end_date is not None:
            df = df[df["date"] <= end_date]
        return df

    def query_kline(
        self,
        codes: Any,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
        period: str = "day",
    ) -> pd.DataFrame:
        df = self._kline_df.copy()
        if isinstance(codes, list):
            df = df[df["code"].isin(codes)]
        else:
            df = df[df["code"] == codes]
        if begin_date is not None:
            df = df[df["date"] >= begin_date]
        if end_date is not None:
            df = df[df["date"] <= end_date]
        return df

    def health(self) -> dict:
        return {"root": "/tmp/data", "duckdb_connected": True}


class FakeKlineResult(NamedTuple):
    df: pd.DataFrame
    source: str = "warehouse"
    from_warehouse: bool = True


class FakeMarketDataService:
    """Fake MarketDataService that reads from FakeWarehouse."""

    def __init__(self, warehouse: FakeWarehouse) -> None:
        self._warehouse = warehouse

    def get_kline(
        self,
        codes: Any,
        begin_date: int,
        end_date: int,
        period: str = "day",
        source: str = "auto",
        **kwargs: Any,
    ) -> FakeKlineResult:
        df = self._warehouse.query_kline(codes, begin_date, end_date, period)
        return FakeKlineResult(df=df)


@pytest.fixture
def stock_client(client, monkeypatch):
    """Provide a TestClient with mocked warehouse and market data service."""
    fake_warehouse = FakeWarehouse()

    # Override FastAPI dependencies in the stock_data router
    client.app.dependency_overrides[deps.get_warehouse_dep] = lambda: fake_warehouse
    client.app.dependency_overrides[deps.get_market_data_service_dep] = (
        lambda: FakeMarketDataService(fake_warehouse)
    )

    yield client

    # Restore
    client.app.dependency_overrides.pop(deps.get_warehouse_dep, None)
    client.app.dependency_overrides.pop(deps.get_market_data_service_dep, None)


# ============================================================
# Response helpers
# ============================================================


def _assert_pro_response(response, expected_code: int = 0):
    """Assert response matches Pro platform format."""
    assert response.status_code == 200
    data = response.json()
    assert "code" in data
    assert "msg" in data
    assert "data" in data
    assert data["code"] == expected_code
    if expected_code == 0:
        assert data["data"] is not None
        assert "fields" in data["data"]
        assert "items" in data["data"]
    return data


# ============================================================
# Stock Basic
# ============================================================


class TestStockBasicE2E:
    """End-to-end tests for /stock_basic."""

    def test_stock_basic_default(self, stock_client):
        """Default query should return all stocks."""
        response = stock_client.get("/stock_basic")
        data = _assert_pro_response(response)
        assert len(data["data"]["items"]) == 3
        assert "ts_code" in data["data"]["fields"]
        assert "symbol" in data["data"]["fields"]

    def test_stock_basic_filter_by_ts_code(self, stock_client):
        """Filter by ts_code should return matching stocks."""
        response = stock_client.get("/stock_basic?ts_code=000001.SZ")
        data = _assert_pro_response(response)
        assert len(data["data"]["items"]) == 1
        assert data["data"]["items"][0][0] == "000001.SZ"

    def test_stock_basic_filter_by_exchange(self, stock_client):
        """Filter by exchange should return matching exchange stocks."""
        response = stock_client.get("/stock_basic?exchange=SZSE")
        data = _assert_pro_response(response)
        codes = [item[0] for item in data["data"]["items"]]
        assert all(c.endswith(".SZ") for c in codes)

    def test_stock_basic_filter_by_market(self, stock_client):
        """Filter by market type should return matching stocks."""
        response = stock_client.get("/stock_basic?market=创业板")
        data = _assert_pro_response(response)
        assert len(data["data"]["items"]) == 1
        assert data["data"]["items"][0][0] == "300001.SZ"

    def test_stock_basic_fields_param(self, stock_client):
        """Fields param should restrict returned columns."""
        response = stock_client.get("/stock_basic?fields=ts_code,name")
        data = _assert_pro_response(response)
        assert data["data"]["fields"] == ["ts_code", "name"]
        assert len(data["data"]["items"][0]) == 2


# ============================================================
# Trade Calendar
# ============================================================


class TestTradeCalE2E:
    """End-to-end tests for /trade_cal."""

    def test_trade_cal_default(self, stock_client):
        """Default query should return calendar data."""
        response = stock_client.get("/trade_cal")
        data = _assert_pro_response(response)
        assert len(data["data"]["items"]) == 5
        assert "exchange" in data["data"]["fields"]
        assert "cal_date" in data["data"]["fields"]

    def test_trade_cal_filter_by_date_range(self, stock_client):
        """Date range filter should restrict results."""
        response = stock_client.get("/trade_cal?start_date=20260608&end_date=20260610")
        data = _assert_pro_response(response)
        assert len(data["data"]["items"]) == 3

    def test_trade_cal_filter_by_is_open(self, stock_client):
        """is_open filter should return only open/closed days."""
        response = stock_client.get("/trade_cal?is_open=1")
        data = _assert_pro_response(response)
        assert all(item[2] == 1 for item in data["data"]["items"])


# ============================================================
# Daily / Weekly / Monthly
# ============================================================


class TestKlineE2E:
    """End-to-end tests for /daily, /weekly, /monthly."""

    def test_daily_default(self, stock_client):
        """Default daily query should return kline data."""
        response = stock_client.get("/daily?ts_code=000001.SZ")
        data = _assert_pro_response(response)
        assert len(data["data"]["items"]) == 5
        assert "ts_code" in data["data"]["fields"]
        assert "trade_date" in data["data"]["fields"]
        assert "pre_close" in data["data"]["fields"]
        assert "pct_chg" in data["data"]["fields"]
        assert "vol" in data["data"]["fields"]

    def test_daily_filter_by_trade_date(self, stock_client):
        """Filter by trade_date should return single day."""
        response = stock_client.get("/daily?ts_code=000001.SZ&trade_date=20260610")
        data = _assert_pro_response(response)
        assert len(data["data"]["items"]) == 1
        assert data["data"]["items"][0][1] == 20260610

    def test_daily_filter_by_date_range(self, stock_client):
        """Date range filter should restrict results."""
        response = stock_client.get(
            "/daily?ts_code=000001.SZ&start_date=20260609&end_date=20260611"
        )
        data = _assert_pro_response(response)
        assert len(data["data"]["items"]) == 3

    def test_daily_fields_param(self, stock_client):
        """Fields param should restrict returned columns."""
        response = stock_client.get("/daily?ts_code=000001.SZ&fields=ts_code,close,vol")
        data = _assert_pro_response(response)
        assert data["data"]["fields"] == ["ts_code", "close", "vol"]

    def test_weekly_endpoint(self, stock_client):
        """Weekly endpoint should work with period=week."""
        response = stock_client.get("/weekly?ts_code=000001.SZ")
        data = _assert_pro_response(response)
        assert "ts_code" in data["data"]["fields"]

    def test_monthly_endpoint(self, stock_client):
        """Monthly endpoint should work with period=month."""
        response = stock_client.get("/monthly?ts_code=000001.SZ")
        data = _assert_pro_response(response)
        assert "ts_code" in data["data"]["fields"]


# ============================================================
# Adj Factor
# ============================================================


class TestAdjFactorE2E:
    """End-to-end tests for /adj_factor."""

    def test_adj_factor_default(self, stock_client):
        """Default adj_factor query should return adj_factor data."""
        response = stock_client.get("/adj_factor?ts_code=000001.SZ")
        data = _assert_pro_response(response)
        assert len(data["data"]["items"]) == 5
        assert "ts_code" in data["data"]["fields"]
        assert "trade_date" in data["data"]["fields"]
        assert "adj_factor" in data["data"]["fields"]


# ============================================================
# Pro Bar
# ============================================================


class TestProBarE2E:
    """End-to-end tests for /pro_bar."""

    def test_pro_bar_default(self, stock_client):
        """Default pro_bar query should return OHLCV data."""
        response = stock_client.get("/pro_bar?ts_code=000001.SZ")
        data = _assert_pro_response(response)
        assert len(data["data"]["items"]) == 5
        assert "ts_code" in data["data"]["fields"]

    def test_pro_bar_with_ma(self, stock_client):
        """pro_bar with ma param should include MA columns."""
        response = stock_client.get("/pro_bar?ts_code=000001.SZ&ma=5,10")
        data = _assert_pro_response(response)
        assert "ma5" in data["data"]["fields"]
        assert "ma10" in data["data"]["fields"]

    def test_pro_bar_with_adj(self, stock_client):
        """pro_bar with adj param should apply adjustment."""
        response = stock_client.get("/pro_bar?ts_code=000001.SZ&adj=qfq")
        data = _assert_pro_response(response)
        assert len(data["data"]["items"]) == 5


# ============================================================
# Suspend
# ============================================================


class TestSuspendE2E:
    """End-to-end tests for /suspend_d."""

    def test_suspend_d_empty(self, stock_client):
        """When no suspensions exist, should return empty items."""
        response = stock_client.get("/suspend_d?ts_code=000001.SZ")
        data = _assert_pro_response(response)
        assert data["data"]["items"] == []


# ============================================================
# Limit List
# ============================================================


class TestLimitListE2E:
    """End-to-end tests for /limit_list."""

    @pytest.mark.skip(reason="limit_list depends on limit-up/down services; tested in service unit tests")
    def test_limit_list_default(self, stock_client):
        """Default limit_list should return Pro format response."""
        response = stock_client.get("/limit_list")
        data = _assert_pro_response(response)
        assert "ts_code" in data["data"]["fields"]
        assert "name" in data["data"]["fields"]


# ============================================================
# New Share
# ============================================================


class TestNewShareE2E:
    """End-to-end tests for /new_share."""

    def test_new_share_with_date_range(self, stock_client):
        """new_share with date range should return stocks listed in range."""
        response = stock_client.get("/new_share?start_date=19910401&end_date=19910430")
        data = _assert_pro_response(response)
        assert "ts_code" in data["data"]["fields"]
        codes = [item[0] for item in data["data"]["items"]]
        assert "000001.SZ" in codes
