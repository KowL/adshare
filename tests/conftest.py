"""Shared fixtures and mocks for adshare tests."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from adshare.main import create_app


class FakeAdapter:
    """Mock AmazingData adapter for integration tests.

    Does not import the real SDK, so it runs on any platform (including ARM Mac).
    """

    def __init__(self):
        self._login_info = {"status": True, "timestamp": 0}
        self._codes = ["000001.SZ", "600000.SH", "000002.SZ"]

    @property
    def is_logged_in(self) -> bool:
        return True

    @property
    def login_info(self):
        return self._login_info

    def login(self) -> bool:
        return True

    def logout(self) -> None:
        self._login_info = None

    def get_code_list(self, security_type: str = "EXTRA_STOCK_A") -> list:
        return self._codes

    def get_code_info(self, security_type: str = "EXTRA_STOCK_A") -> pd.DataFrame:
        return pd.DataFrame(
            {
                "code": self._codes,
                "name": ["平安银行", "浦发银行", "万科A"],
            },
        )

    def get_calendar(self, market: str = "SH", date=None) -> pd.DataFrame:
        dates = [20240102, 20240103, 20240104, 20240607]
        if date is not None:
            dates = [d for d in dates if d == date]
        return pd.DataFrame({"date": dates})

    def get_kline(
        self,
        codes: str,
        begin_date: int,
        end_date: int,
        period: str = "day",
        limit=None,
        offset: int = 0,
    ) -> pd.DataFrame:
        rows = []
        code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]
        for code in code_list:
            if code not in self._codes:
                continue
            for i in range(10):
                rows.append(
                    {
                        "code": code,
                        "kline_time": pd.Timestamp("2024-01-02") + pd.Timedelta(days=i),
                        "open": 10.0 + i * 0.1,
                        "high": 10.5 + i * 0.1,
                        "low": 9.8 + i * 0.1,
                        "close": 10.2 + i * 0.1,
                        "volume": 100000 + i * 1000,
                        "amount": 1000000.0 + i * 10000,
                    }
                )
        df = pd.DataFrame(rows)
        if limit is not None:
            df = df.iloc[offset : offset + limit]
        return df

    def get_snapshot(
        self,
        codes: str,
        date=None,
        time=None,
    ) -> pd.DataFrame:
        code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]
        rows = []
        for code in code_list:
            rows.append(
                {
                    "code": code,
                    "date": date or 20240607,
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.5,
                    "close": 10.8,
                    "pre_close": 9.8,
                    "volume": 500000,
                    "amount": 5400000.0,
                }
            )
        return pd.DataFrame(rows)

    def get_stock_basic(
        self, codes=None, summary_only=False
    ) -> pd.DataFrame:
        df = pd.DataFrame(
            {
                "code": self._codes,
                "name": ["平安银行", "浦发银行", "万科A"],
                "comp_name": ["平安银行股份有限公司", "上海浦东发展银行股份有限公司", "万科企业股份有限公司"],
                "list_date": [19910403, 19991110, 19910129],
                "delist_date": [None, None, None],
                "list_plate": ["主板", "主板", "主板"],
                "is_listed": [1, 1, 1],
            }
        )
        if codes:
            code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]
            df = df[df["code"].isin(code_list)]
        return df

    def get_financial(
        self,
        codes: str,
        statement_type: str = "balance",
        begin_date=None,
        end_date=None,
    ) -> pd.DataFrame:
        code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]
        rows = []
        for code in code_list:
            rows.append(
                {
                    "MARKET_CODE": code,
                    "REPORTING_PERIOD": 20240331,
                    "TOTAL_ASSETS": 5000000000.0,
                    "TOT_SHARE_EQUITY_EXCL_MIN_INT": 3000000000.0,
                }
            )
        return pd.DataFrame(rows)

    def get_shareholder(
        self,
        codes: str,
        begin_date=None,
        end_date=None,
    ) -> pd.DataFrame:
        code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]
        rows = []
        for code in code_list:
            rows.append(
                {
                    "MARKET_CODE": code,
                    "HOLDER_ENDDATE": 20240331,
                    "HOLDER_NUM": 150000,
                }
            )
        return pd.DataFrame(rows)

    def health(self) -> dict:
        return {"sdk_installed": True, "logged_in": True, "login_info": self._login_info}


@pytest.fixture
def fake_adapter():
    """Provide a fresh FakeAdapter instance."""
    return FakeAdapter()


@pytest.fixture
def client(fake_adapter, monkeypatch):
    """Create TestClient with mocked services.

    Replaces the production MarketDataService and limit-up service factories
    so tests run without the real SDK or a populated L3 warehouse.
    """
    import adshare.core.cache as _cache_mod
    import adshare.historical.warehouse as _wh_mod
    import adshare.services.market_data as _md_mod
    import adshare.services.technical_analysis as _ta_mod
    import adshare.services.limit_up as _lu_mod

    # Disable the L3 warehouse and the scheduler for these tests
    monkeypatch.setenv("HISTORICAL_ENABLED", "false")
    monkeypatch.setenv("SYNC_SCHEDULE_ENABLED", "false")
    from adshare.core.config import get_settings
    get_settings.cache_clear()
    _wh_mod.reset_warehouse()
    _cache_mod._cache_manager = None

    # Preserve originals
    _orig_get_market_data_service = _md_mod.get_market_data_service
    _orig_get_limit_up_service = _lu_mod.get_limit_up_service
    _orig_get_limit_down_service = _lu_mod.get_limit_down_service
    _orig_get_market_activity_service = _lu_mod.get_market_activity_service
    _orig_get_strong_stock_pool_service = _lu_mod.get_strong_stock_pool_service
    _orig_get_technical_analysis_service = getattr(_ta_mod, "get_technical_analysis_service", None)

    # Fake MarketDataService backed by FakeAdapter
    class FakeMarketDataService(_md_mod.MarketDataService):
        def __init__(self):
            self._adapter = fake_adapter

        def get_code_list(self, security_type="EXTRA_STOCK_A"):
            return self._adapter.get_code_list(security_type)

        def get_calendar(self, market="SH", date=None):
            return self._adapter.get_calendar(market, date)

        def get_kline(self, codes, begin_date, end_date, period="day", limit=None, offset=0, source="auto"):
            df = self._adapter.get_kline(codes, begin_date, end_date, period, limit, offset)
            return _md_mod.KlineQueryResult(df=df, source="warehouse", synced=True)

        def get_snapshot(self, codes, date=None, time=None):
            return self._adapter.get_snapshot(codes, date, time)

        def get_stock_basic(self, codes=None, summary_only=False):
            return self._adapter.get_stock_basic(codes, summary_only)

    fake_md = FakeMarketDataService()
    _md_mod.get_market_data_service = lambda: fake_md

    # Patch module-local references created by ``from ... import ...``
    import adshare.routers.market as _market_router_mod
    import adshare.routers.historical as _historical_router_mod
    import adshare.routers.technical as _technical_router_mod

    # Preserve router module originals BEFORE patching
    _orig_market_router_md = _market_router_mod.get_market_data_service
    _orig_market_router_lu = _market_router_mod.get_limit_up_service
    _orig_market_router_ld = _market_router_mod.get_limit_down_service
    _orig_market_router_ma = _market_router_mod.get_market_activity_service
    _orig_market_router_sp = _market_router_mod.get_strong_stock_pool_service
    _orig_historical_router_md = _historical_router_mod.get_market_data_service
    _orig_technical_router_ta = _technical_router_mod.get_technical_analysis_service

    _market_router_mod.get_market_data_service = lambda: fake_md
    _historical_router_mod.get_market_data_service = lambda: fake_md
    _technical_router_mod.get_technical_analysis_service = lambda: _ta_mod.TechnicalAnalysisService(
        market_data_service=fake_md
    )

    # Fake limit-up services backed by FakeAdapter
    class FakeLimitUpService(_lu_mod.LimitUpService):
        def __init__(self):
            self.adapter = fake_adapter
            self.warehouse = None
            self.batch_size = 200

        def _get_code_info(self):
            return fake_adapter.get_code_info()

        def _get_daily_kline(self, codes, target_date):
            begin_date = _lu_mod._lookback_begin_date(target_date)
            df = fake_adapter.get_kline(",".join(codes), begin_date, target_date, "day")
            if "kline_time" in df.columns:
                df = df.copy()
                df["date"] = df["kline_time"].dt.strftime("%Y%m%d").astype(int)
            return df

    class FakeLimitDownService(_lu_mod.LimitDownService):
        def __init__(self):
            self.adapter = fake_adapter
            self.warehouse = None
            self.batch_size = 200

        def _get_code_info(self):
            return fake_adapter.get_code_info()

        def _get_daily_kline(self, codes, target_date):
            begin_date = _lu_mod._lookback_begin_date(target_date)
            df = fake_adapter.get_kline(",".join(codes), begin_date, target_date, "day")
            if "kline_time" in df.columns:
                df = df.copy()
                df["date"] = df["kline_time"].dt.strftime("%Y%m%d").astype(int)
            return df

    class FakeMarketActivityService(_lu_mod.MarketActivityService):
        def __init__(self):
            self._base = FakeLimitUpService()

    class FakeStrongStockPoolService(_lu_mod.StrongStockPoolService):
        def __init__(self):
            self.adapter = fake_adapter
            self.warehouse = None
            self.batch_size = 200
            self._base = FakeLimitUpService()

        def _get_kline_range(self, codes, begin_date, end_date):
            df = fake_adapter.get_kline(",".join(codes), begin_date, end_date, "day")
            if "kline_time" in df.columns:
                df = df.copy()
                df["date"] = df["kline_time"].dt.strftime("%Y%m%d").astype(int)
            return df

    _lu_mod.get_limit_up_service = lambda: FakeLimitUpService()
    _lu_mod.get_limit_down_service = lambda: FakeLimitDownService()
    _lu_mod.get_market_activity_service = lambda: FakeMarketActivityService()
    _lu_mod.get_strong_stock_pool_service = lambda: FakeStrongStockPoolService()

    # Patch limit-up service references in market router
    _market_router_mod.get_limit_up_service = lambda: FakeLimitUpService()
    _market_router_mod.get_limit_down_service = lambda: FakeLimitDownService()
    _market_router_mod.get_market_activity_service = lambda: FakeMarketActivityService()
    _market_router_mod.get_strong_stock_pool_service = lambda: FakeStrongStockPoolService()

    # Technical analysis service
    if _orig_get_technical_analysis_service is not None:
        _ta_mod.get_technical_analysis_service = lambda: _ta_mod.TechnicalAnalysisService(
            market_data_service=fake_md
        )

    try:
        app = create_app()
        with TestClient(app) as tc:
            yield tc
    finally:
        _wh_mod.reset_warehouse()
        _cache_mod._cache_manager = None
        _md_mod.get_market_data_service = _orig_get_market_data_service
        _lu_mod.get_limit_up_service = _orig_get_limit_up_service
        _lu_mod.get_limit_down_service = _orig_get_limit_down_service
        _lu_mod.get_market_activity_service = _orig_get_market_activity_service
        _lu_mod.get_strong_stock_pool_service = _orig_get_strong_stock_pool_service
        if _orig_get_technical_analysis_service is not None:
            _ta_mod.get_technical_analysis_service = _orig_get_technical_analysis_service
        # Restore router module references
        _market_router_mod.get_market_data_service = _orig_market_router_md
        _market_router_mod.get_limit_up_service = _orig_market_router_lu
        _market_router_mod.get_limit_down_service = _orig_market_router_ld
        _market_router_mod.get_market_activity_service = _orig_market_router_ma
        _market_router_mod.get_strong_stock_pool_service = _orig_market_router_sp
        _historical_router_mod.get_market_data_service = _orig_historical_router_md
        _technical_router_mod.get_technical_analysis_service = _orig_technical_router_ta
