"""Tests for the market data application service (API-only mode)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from adshare.core.config import get_settings
from adshare.services.market_data import MarketDataService


@pytest.fixture
def historical_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("HISTORICAL_ENABLED", "true")
    monkeypatch.setenv("HISTORICAL_PATH", str(tmp_path / "historical"))
    monkeypatch.setenv("SYNC_SCHEDULE_ENABLED", "false")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def _kline_df(code: str = "000001.SZ") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": [code],
            "date": [20240102],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "volume": [100000],
            "amount": [1_000_000.0],
        }
    )


def test_get_kline_uses_warehouse_when_synced(historical_settings):
    warehouse = MagicMock()
    warehouse.is_synced.return_value = True
    warehouse.query_kline.return_value = _kline_df()

    service = MarketDataService(
        settings=historical_settings,
        warehouse=warehouse,
    )
    result = service.get_kline(
        codes="000001.SZ",
        begin_date=20240101,
        end_date=20240131,
        period="day",
    )

    assert result.source == "warehouse"
    assert result.synced is True
    assert len(result.df) == 1


def test_get_kline_returns_empty_when_warehouse_not_synced(historical_settings):
    warehouse = MagicMock()
    warehouse.is_synced.return_value = False

    service = MarketDataService(
        settings=historical_settings,
        warehouse=warehouse,
    )
    result = service.get_kline(
        codes=["000001.SZ"],
        begin_date=20240101,
        end_date=20240131,
        period="day",
        source="auto",
    )

    assert result.source == "warehouse"
    assert result.synced is False
    assert result.df.empty


def test_get_kline_source_warehouse_does_not_fallback(historical_settings):
    warehouse = MagicMock()
    warehouse.is_synced.return_value = False

    service = MarketDataService(
        settings=historical_settings,
        warehouse=warehouse,
    )
    result = service.get_kline(
        codes="000001.SZ",
        begin_date=20240101,
        end_date=20240131,
        period="day",
        source="warehouse",
    )

    assert result.source == "warehouse"
    assert result.synced is False
    assert result.df.empty


def test_get_code_list_uses_local_metadata(historical_settings):
    warehouse = MagicMock()
    warehouse.query_codes.return_value = pd.DataFrame({"code": ["000001.SZ"], "is_listed": [True]})
    service = MarketDataService(settings=historical_settings, warehouse=warehouse)

    assert service.get_code_list("EXTRA_STOCK_A") == ["000001.SZ"]


def test_get_code_list_returns_empty_when_no_local_data(historical_settings):
    warehouse = MagicMock()
    warehouse.query_codes.return_value = pd.DataFrame()
    service = MarketDataService(settings=historical_settings, warehouse=warehouse)

    assert service.get_code_list("EXTRA_STOCK_A") == []


def test_get_calendar_uses_local_data(historical_settings):
    warehouse = MagicMock()
    warehouse.query_calendar.return_value = pd.DataFrame({"date": [20240102]})
    service = MarketDataService(settings=historical_settings, warehouse=warehouse)

    df = service.get_calendar(market="SH", date=20240102)

    assert list(df["date"]) == [20240102]


def test_get_snapshot_returns_empty_in_api_only_mode(historical_settings):
    service = MarketDataService(settings=historical_settings)

    df = service.get_snapshot(codes="000001.SZ")

    assert df.empty


def test_get_stock_basic_uses_local_data(historical_settings):
    warehouse = MagicMock()
    warehouse.query_codes.return_value = pd.DataFrame({"code": ["000001.SZ"], "name": ["平安银行"]})
    service = MarketDataService(settings=historical_settings, warehouse=warehouse)

    df = service.get_stock_basic(codes="000001.SZ", summary_only=False)

    assert list(df["code"]) == ["000001.SZ"]
