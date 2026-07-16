"""Tests for tushare compatible endpoints."""

import pytest


class TestTushareStockDaily:
    def test_daily_restful_post(self, client):
        response = client.post(
            "/tushare/stock/daily",
            json={"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240110"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "data" in data
        assert "fields" in data["data"]
        assert "items" in data["data"]
        assert "ts_code" in data["data"]["fields"]
        assert "trade_date" in data["data"]["fields"]

    def test_daily_restful_get(self, client):
        response = client.get(
            "/tushare/stock/daily",
            params={"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240110"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert len(data["data"]["items"]) > 0

    def test_daily_unified_entry(self, client):
        response = client.post(
            "/tushare",
            json={
                "api_name": "daily",
                "params": {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240110"},
                "fields": "",
                "token": "",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "ts_code" in data["data"]["fields"]

    def test_daily_missing_ts_code(self, client):
        response = client.post("/tushare/stock/daily", json={})
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == -1

    def test_daily_invalid_date(self, client):
        response = client.post(
            "/tushare/stock/daily",
            json={"ts_code": "000001.SZ", "start_date": "not-a-date", "end_date": "20240110"},
        )
        assert response.status_code == 400

    def test_daily_fields_filter(self, client):
        response = client.post(
            "/tushare/stock/daily",
            json={
                "ts_code": "000001.SZ",
                "start_date": "20240101",
                "end_date": "20240110",
                "fields": "ts_code,trade_date,close",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["fields"] == ["ts_code", "trade_date", "close"]


class TestTushareStockBasic:
    def test_stock_basic(self, client):
        response = client.get("/tushare/stock/stock_basic")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "ts_code" in data["data"]["fields"]
        assert len(data["data"]["items"]) > 0

    def test_stock_basic_filter_by_ts_code(self, client):
        response = client.get(
            "/tushare/stock/stock_basic",
            params={"ts_code": "000001.SZ"},
        )
        assert response.status_code == 200
        data = response.json()
        items = data["data"]["items"]
        assert len(items) == 1


class TestTushareTradeCal:
    def test_trade_cal(self, client):
        response = client.get("/tushare/stock/trade_cal")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "cal_date" in data["data"]["fields"]


class TestTushareAdjFactor:
    def test_adj_factor(self, client):
        response = client.post(
            "/tushare/stock/adj_factor",
            json={"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240110"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestTushareSuspendD:
    def test_suspend_d(self, client):
        response = client.post(
            "/tushare/stock/suspend_d",
            json={"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240110"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestTushareLimitList:
    def test_limit_list(self, client):
        response = client.get("/tushare/stock/limit_list")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestTushareUnifiedUnsupported:
    def test_unsupported_api(self, client):
        response = client.post(
            "/tushare",
            json={"api_name": "not_real", "params": {}, "token": ""},
        )
        assert response.status_code == 501


class TestTushareIndexReserved:
    def test_index_basic_reserved(self, client):
        response = client.get("/tushare/index/basic")
        assert response.status_code == 501


class TestTushareDeprecatedDataapi:
    def test_dataapi_gone(self, client):
        response = client.post("/dataapi/daily", json={"ts_code": "000001.SZ"})
        assert response.status_code == 404
