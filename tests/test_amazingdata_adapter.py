"""Contract tests for AmazingData adapter SDK calls."""

from types import SimpleNamespace

import pandas as pd

from amazingdata.adapters.amazingdata import AmazingDataAdapter


class FakeBaseData:
    def __init__(self) -> None:
        self.code_list_security_type = None
        self.calendar_markets = []
        self.code_info_security_type = None

    def get_code_list(self, security_type: str = "EXTRA_STOCK_A"):
        self.code_list_security_type = security_type
        return ["510300.SH"]

    def get_code_info(self, security_type: str = "EXTRA_STOCK_A"):
        self.code_info_security_type = security_type
        return pd.DataFrame({"symbol": ["沪深300ETF"]}, index=["510300.SH"])

    def get_calendar(self, market: str = "SH"):
        self.calendar_markets.append(market)
        return [20240102, 20240103]


class NoMarketCalendarBaseData(FakeBaseData):
    def get_calendar(self, market: str = "SH"):
        if market != "SH":
            raise TypeError("get_calendar() got an unexpected keyword argument 'market'")
        return [20240102, 20240103]


def make_adapter(base_data) -> AmazingDataAdapter:
    adapter = object.__new__(AmazingDataAdapter)
    adapter.settings = SimpleNamespace(ad_max_retries=1, ad_retry_delay=0)
    adapter._base_data = base_data
    adapter._market_data = None
    adapter._client = object()
    adapter._login_info = {"status": True}
    adapter.ensure_login = lambda: True
    adapter._get_client = lambda: adapter._client
    adapter._ensure_base_data = lambda: None
    return adapter


def test_get_code_list_passes_requested_security_type_to_base_data():
    base_data = FakeBaseData()
    adapter = make_adapter(base_data)

    result = adapter.get_code_list(security_type="EXTRA_ETF")

    assert result == ["510300.SH"]
    assert base_data.code_list_security_type == "EXTRA_ETF"


def test_get_code_info_uses_base_data_security_type():
    base_data = FakeBaseData()
    adapter = make_adapter(base_data)

    result = adapter.get_code_info(security_type="EXTRA_ETF")

    assert result.index.tolist() == ["510300.SH"]
    assert base_data.code_info_security_type == "EXTRA_ETF"


def test_get_calendar_passes_market_when_sdk_supports_it():
    base_data = FakeBaseData()
    adapter = make_adapter(base_data)

    result = adapter.get_calendar(market="SZ")

    assert result["date"].tolist() == [20240102, 20240103]
    assert base_data.calendar_markets == ["SZ"]


def test_get_calendar_falls_back_for_sdk_without_market_argument():
    base_data = NoMarketCalendarBaseData()
    adapter = make_adapter(base_data)

    result = adapter.get_calendar(market="SZ")

    assert result["date"].tolist() == [20240102, 20240103]
