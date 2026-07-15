"""Tests for the tushare compatible client."""

import httpx
import pandas as pd
import pytest

from adshare.clients.tushare_client import TushareApiError, TushareAuthError, TushareClient


class MockTushareTransport(httpx.MockTransport):
    """Return a canned tushare Pro response for any request."""

    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request):
        return httpx.Response(self.status_code, json=self.payload)


def test_client_query_returns_dataframe():
    payload = {
        "code": 0,
        "msg": "",
        "data": {
            "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
            "items": [
                ["000001.SZ", 20240102, 10.0, 10.5, 9.8, 10.2, 1000, 10200.0],
                ["000001.SZ", 20240103, 10.2, 10.6, 10.0, 10.4, 1100, 10500.0],
            ],
        },
    }
    transport = MockTushareTransport(payload)
    client = TushareClient(base_url="http://test/tushare", http_client=httpx.Client(transport=transport))
    df = client.query("daily", ts_code="000001.SZ", start_date="20240101", end_date="20240110")

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == payload["data"]["fields"]
    assert len(df) == 2
    assert df["ts_code"].iloc[0] == "000001.SZ"
    assert df["trade_date"].iloc[0] == 20240102


def test_client_daily_method():
    payload = {
        "code": 0,
        "msg": "",
        "data": {"fields": ["ts_code", "trade_date", "close"], "items": [["000001.SZ", 20240102, 10.2]]},
    }
    transport = MockTushareTransport(payload)
    client = TushareClient(base_url="http://test/tushare", http_client=httpx.Client(transport=transport))
    df = client.daily(ts_code="000001.SZ", start_date="20240101", end_date="20240110")

    assert isinstance(df, pd.DataFrame)
    assert "close" in df.columns


def test_client_empty_response():
    payload = {"code": 0, "msg": "", "data": {"fields": [], "items": []}}
    transport = MockTushareTransport(payload)
    client = TushareClient(base_url="http://test/tushare", http_client=httpx.Client(transport=transport))
    df = client.query("daily", ts_code="000001.SZ")

    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_client_api_error():
    payload = {"code": -1, "msg": "invalid ts_code", "data": None}
    transport = MockTushareTransport(payload, status_code=200)
    client = TushareClient(base_url="http://test/tushare", http_client=httpx.Client(transport=transport))

    with pytest.raises(TushareApiError) as exc_info:
        client.query("daily", ts_code="bad_code")

    assert "invalid ts_code" in str(exc_info.value)


def test_client_http_error():
    transport = MockTushareTransport({}, status_code=500)
    client = TushareClient(base_url="http://test/tushare", http_client=httpx.Client(transport=transport))

    with pytest.raises(TushareApiError) as exc_info:
        client.query("daily", ts_code="000001.SZ")

    assert exc_info.value.status_code == 500


def test_client_auth_error():
    transport = MockTushareTransport({}, status_code=401)
    client = TushareClient(base_url="http://test/tushare", http_client=httpx.Client(transport=transport))

    with pytest.raises(TushareAuthError):
        client.query("daily", ts_code="000001.SZ")


def test_project_root_tushare_module():
    """Smoke test for the independent project-root tushare.py shim."""
    import tushare as ts

    assert callable(ts.pro_api)
    assert callable(ts.set_token)

    payload = {
        "code": 0,
        "msg": "",
        "data": {
            "fields": ["ts_code", "trade_date", "close"],
            "items": [["000001.SZ", 20240102, 10.2]],
        },
    }
    transport = MockTushareTransport(payload)
    http_client = httpx.Client(transport=transport)
    pro = ts.pro_api("http://test/tushare", http_client=http_client)

    df = pro.daily(ts_code="000001.SZ", start_date="20240101", end_date="20240110")
    assert isinstance(df, pd.DataFrame)
    assert "close" in df.columns

    ts.set_token("test-token")
    assert ts.get_token() == "test-token"
