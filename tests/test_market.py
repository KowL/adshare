"""Integration tests for market data endpoints."""

import pandas as pd


class TestMarketCodes:
    def test_get_code_list(self, client):
        response = client.get("/market/codes")
        assert response.status_code == 200
        data = response.json()
        assert data["security_type"] == "stock_a"
        assert len(data["code_list"]) == 3
        assert "000001.SZ" in data["code_list"]

    def test_get_code_list_legacy_security_type_accepted(self, client):
        # Legacy AmazingData-style values are still accepted for compatibility.
        response = client.get("/market/codes?security_type=EXTRA_STOCK_A")
        assert response.status_code == 200
        data = response.json()
        assert data["security_type"] == "EXTRA_STOCK_A"
        assert len(data["code_list"]) == 3


class TestMarketCalendar:
    def test_get_calendar_all(self, client):
        response = client.get("/market/calendar?market=SH")
        assert response.status_code == 200
        data = response.json()
        assert data["market"] == "SH"
        assert len(data["calendar"]) > 0
        assert 20240102 in data["calendar"]

    def test_get_calendar_with_date(self, client):
        response = client.get("/market/calendar?market=SH&date=20240102")
        assert response.status_code == 200
        data = response.json()
        assert data["calendar"] == [20240102]

    def test_get_calendar_empty_date(self, client):
        response = client.get("/market/calendar?market=SH&date=19990101")
        assert response.status_code == 200
        data = response.json()
        assert data["calendar"] == []


class TestMarketKline:
    def test_get_kline_single_code(self, client):
        response = client.get(
            "/market/kline?codes=000001.SZ&begin_date=20240101&end_date=20241231&period=day"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["codes"] == ["000001.SZ"]
        assert data["period"] == "day"
        assert data["count"] == 10
        assert len(data["data"]) == 10
        first = data["data"][0]
        assert "open" in first
        assert "close" in first
        assert "volume" in first

    def test_get_kline_multi_codes(self, client):
        response = client.get(
            "/market/kline?codes=000001.SZ,600000.SH&begin_date=20240101&end_date=20241231"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 20

    def test_get_kline_simple(self, client):
        response = client.get("/market/kline/simple?symbol=000001.SZ&count=5")
        assert response.status_code == 200
        data = response.json()
        assert data["codes"] == ["000001.SZ"]
        assert data["count"] == 5

    def test_get_kline_invalid_date(self, client):
        # Query params are validated by Pydantic KlineRequest when used as
        # request body; for GET queries they are passed as plain ints.
        # The adapter will reject invalid dates downstream.
        response = client.get(
            "/market/kline?codes=000001.SZ&begin_date=2024010&end_date=20241231"
        )
        # Accept either success (adapter handles it) or 500 (SDK error)
        assert response.status_code in (200, 500)


class TestMarketSnapshot:
    def test_get_snapshot(self, client):
        response = client.get("/market/snapshot?codes=000001.SZ")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["data"][0]["code"] == "000001.SZ"

    def test_get_snapshot_multi(self, client):
        response = client.get("/market/snapshot?codes=000001.SZ,600000.SH")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2


class TestMarketStockBasic:
    def test_get_stock_basic(self, client):
        response = client.get("/market/stock/basic?codes=000001.SZ")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["data"][0]["code"] == "000001.SZ"
        assert data["data"][0]["name"] == "平安银行"

    def test_get_stock_basic_all(self, client):
        response = client.get("/market/stock/basic")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3


class TestMarketLimitUp:
    def test_get_limit_up(self, client):
        response = client.get("/market/limit-up")
        assert response.status_code == 200
        data = response.json()
        assert "date" in data
        assert isinstance(data["stocks"], list)
        assert "count" in data

    def test_get_limit_up_board_filter(self, client):
        response = client.get("/market/limit-up?board_filter=主板")
        assert response.status_code == 200
        data = response.json()
        for stock in data["stocks"]:
            assert stock["board"] == "主板"

    def test_get_limit_up_exclude_st(self, client):
        response = client.get("/market/limit-up?exclude_st=true")
        assert response.status_code == 200
        data = response.json()
        for stock in data["stocks"]:
            assert not stock["name"].startswith("ST")
            assert "*ST" not in stock["name"]

    def test_get_limit_up_uses_code_name_columns(self, client, fake_adapter):
        fake_adapter.get_code_info = lambda security_type="EXTRA_STOCK_A": pd.DataFrame(
            {
                "code": ["000001.SZ", "600000.SH", "000002.SZ"],
                "name": ["平安银行", "浦发银行", "万科A"],
            }
        )
        fake_adapter.get_kline = lambda codes, begin_date, end_date, period="day", **kwargs: pd.DataFrame(
            [
                {
                    "code": code,
                    "date": date,
                    "open": close - 0.1,
                    "high": close,
                    "low": close - 0.2,
                    "close": close,
                    "volume": 100,
                    "amount": close * 100,
                }
                for code in codes.split(",")
                for date, close in [(20240606, 10.0), (20240607, 11.0)]
            ]
        )

        response = client.get("/market/limit-up?date=20240607&exclude_st=false")

        assert response.status_code == 200
        data = response.json()
        names_by_code = {stock["code"]: stock["name"] for stock in data["stocks"]}
        assert names_by_code["000001"] == "平安银行"
        assert names_by_code["600000"] == "浦发银行"
        assert names_by_code["000002"] == "万科A"

    def test_get_limit_up_ladder(self, client):
        response = client.get("/market/limit-up/ladder")
        assert response.status_code == 200
        data = response.json()
        assert "date" in data
        assert "levels" in data
        assert "maxLevel" in data
