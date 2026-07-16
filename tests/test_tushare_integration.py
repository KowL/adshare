"""Integration tests for the project-root ``tushare.py`` adapter against a live adshare API.

These tests require a running adshare-api instance. Start one locally with::

    docker compose up -d adshare-api

Then run::

    .venv/bin/python -m pytest tests/test_tushare_integration.py -v

Environment variables:
    TUSHARE_INTEGRATION_URL: adshare tushare endpoint (default: http://localhost:8000/tushare)
    ADSHARE_HEALTH_URL: adshare health endpoint (default: http://localhost:8000/health)
"""

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

# Ensure the project-root tushare.py adapter is loaded before the real tushare
# package installed in the virtual environment.
_PROJECT_ROOT = str(Path(__file__).parent.parent)
sys.path.insert(0, _PROJECT_ROOT)

import tushare as ts  # noqa: E402


BASE_URL = os.getenv("TUSHARE_INTEGRATION_URL", "http://localhost:8000/tushare")
HEALTH_URL = os.getenv("ADSHARE_HEALTH_URL", "http://localhost:8000/health")


def _service_available() -> bool:
    """Check whether the adshare-api health endpoint is reachable."""
    try:
        from urllib import request

        with request.urlopen(HEALTH_URL, timeout=2) as resp:  # noqa: S310
            return resp.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pro():
    """Return a TushareProApi instance pointing at the local adshare API."""
    if not _service_available():
        pytest.skip(f"adshare-api is not available at {HEALTH_URL}")
    return ts.pro_api(BASE_URL)


class TestTushareAdapterIntegration:
    """End-to-end tests using the real ``tushare`` package through the adapter."""

    def test_daily_returns_dataframe_with_expected_columns(self, pro):
        df = pro.daily(ts_code="000001.SZ", start_date="20240101", end_date="20240131")
        assert isinstance(df, pd.DataFrame)
        expected = {"ts_code", "trade_date", "open", "high", "low", "close"}
        assert expected.issubset(set(df.columns)), f"got columns: {list(df.columns)}"

    def test_stock_basic_returns_dataframe(self, pro):
        df = pro.stock_basic()
        assert isinstance(df, pd.DataFrame)
        assert "ts_code" in df.columns

    def test_trade_cal_returns_dataframe(self, pro):
        df = pro.trade_cal(exchange="SSE", start_date="20240101", end_date="20240131")
        assert isinstance(df, pd.DataFrame)
        assert "cal_date" in df.columns

    def test_unsupported_api_raises_tushare_api_error(self, pro):
        with pytest.raises(ts.TushareApiError):
            pro.query("not_a_real_api")
