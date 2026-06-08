"""Tests for the market data application service."""

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
    adapter = MagicMock()
    adapter.get_kline.side_effect = AssertionError("SDK should not be called on L3 hit")

    service = MarketDataService(
        settings=historical_settings,
        adapter=adapter,
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
    adapter.get_kline.assert_not_called()


def test_get_kline_falls_back_to_sdk_when_warehouse_not_synced(historical_settings):
    warehouse = MagicMock()
    warehouse.is_synced.return_value = False
    adapter = MagicMock()
    adapter.get_kline.return_value = _kline_df()

    service = MarketDataService(
        settings=historical_settings,
        adapter=adapter,
        warehouse=warehouse,
    )
    result = service.get_kline(
        codes=["000001.SZ"],
        begin_date=20240101,
        end_date=20240131,
        period="day",
        source="auto",
    )

    assert result.source == "sdk"
    assert result.synced is False
    assert len(result.df) == 1
    adapter.get_kline.assert_called_once()


def test_get_kline_source_warehouse_does_not_fallback(historical_settings):
    warehouse = MagicMock()
    warehouse.is_synced.return_value = False
    adapter = MagicMock()

    service = MarketDataService(
        settings=historical_settings,
        adapter=adapter,
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
    adapter.get_kline.assert_not_called()


def test_get_kline_normalizes_period_alias_for_sdk_fallback(historical_settings):
    warehouse = MagicMock()
    warehouse.is_synced.return_value = False
    adapter = MagicMock()
    adapter.get_kline.return_value = _kline_df()

    service = MarketDataService(
        settings=historical_settings,
        adapter=adapter,
        warehouse=warehouse,
    )
    service.get_kline(
        codes="000001.SZ",
        begin_date=20240101,
        end_date=20240131,
        period="daily",
        source="auto",
    )

    assert adapter.get_kline.call_args.kwargs["period"] == "day"


def test_get_kline_rejects_unknown_source(historical_settings):
    service = MarketDataService(settings=historical_settings, adapter=MagicMock(), warehouse=MagicMock())

    with pytest.raises(ValueError, match="source must be one of"):
        service.get_kline(
            codes="000001.SZ",
            begin_date=20240101,
            end_date=20240131,
            source="unknown",
        )


def test_get_code_list_delegates_to_adapter(historical_settings):
    adapter = MagicMock()
    adapter.get_code_list.return_value = ["000001.SZ"]
    service = MarketDataService(settings=historical_settings, adapter=adapter)

    assert service.get_code_list("EXTRA_STOCK_A") == ["000001.SZ"]
    adapter.get_code_list.assert_called_once_with(security_type="EXTRA_STOCK_A")


def test_get_calendar_delegates_to_adapter(historical_settings):
    adapter = MagicMock()
    adapter.get_calendar.return_value = pd.DataFrame({"date": [20240102]})
    service = MarketDataService(settings=historical_settings, adapter=adapter)

    df = service.get_calendar(market="SH", date=20240102)

    assert list(df["date"]) == [20240102]
    adapter.get_calendar.assert_called_once_with(market="SH", date=20240102)


def test_get_snapshot_returns_empty_when_adapter_not_logged_in(historical_settings):
    adapter = MagicMock()
    adapter.is_logged_in = False
    service = MarketDataService(settings=historical_settings, adapter=adapter)

    df = service.get_snapshot(codes="000001.SZ")

    assert df.empty
    adapter.get_snapshot.assert_not_called()


def test_get_snapshot_delegates_when_adapter_logged_in(historical_settings):
    adapter = MagicMock()
    adapter.is_logged_in = True
    adapter.get_snapshot.return_value = pd.DataFrame({"code": ["000001.SZ"], "date": [20240607]})
    service = MarketDataService(settings=historical_settings, adapter=adapter)

    df = service.get_snapshot(codes="000001.SZ", date=20240607)

    assert list(df["code"]) == ["000001.SZ"]
    adapter.get_snapshot.assert_called_once_with(codes="000001.SZ", date=20240607, time=None)


def test_get_stock_basic_delegates_to_adapter(historical_settings):
    adapter = MagicMock()
    adapter.get_stock_basic.return_value = pd.DataFrame({"code": ["000001.SZ"]})
    service = MarketDataService(settings=historical_settings, adapter=adapter)

    df = service.get_stock_basic(codes="000001.SZ", summary_only=False)

    assert list(df["code"]) == ["000001.SZ"]
    adapter.get_stock_basic.assert_called_once_with(codes="000001.SZ", summary_only=False)
