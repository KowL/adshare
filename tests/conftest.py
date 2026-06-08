"""Shared fixtures and mocks for adshare tests."""

from unittest.mock import MagicMock, patch

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
                "symbol": ["平安银行", "浦发银行", "万科A"],
            },
            index=self._codes,
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
def client(fake_adapter):
    """Create TestClient with mocked adapter.

    Patches every module-level ``from adshare.adapters.amazingdata import get_adapter``
    reference, because ``from ... import ...`` binds a local reference that
    ``mock.patch("module.func")`` will not redirect after the module has loaded.
    """
    import adshare.adapters.amazingdata as _ad_mod
    import adshare.core.cache as _cache_mod
    import adshare.routers.market as _market_mod
    import adshare.routers.financial as _fin_mod
    import adshare.routers.technical as _tech_mod
    import adshare.routers.fundamental as _fund_mod
    import adshare.routers.factor as _factor_mod
    import adshare.routers.health as _health_mod

    # Store originals
    originals = {
        "_adapter": _ad_mod._adapter,
        "market": getattr(_market_mod, "get_adapter", None),
        "financial": getattr(_fin_mod, "get_adapter", None),
        "technical": getattr(_tech_mod, "get_adapter", None),
        "fundamental": getattr(_fund_mod, "get_adapter", None),
        "factor": getattr(_factor_mod, "get_adapter", None),
        "health": getattr(_health_mod, "get_adapter", None),
    }

    # Inject fake adapter
    _ad_mod._adapter = fake_adapter
    _cache_mod._cache_manager = None
    _market_mod.get_adapter = lambda: fake_adapter
    _fin_mod.get_adapter = lambda: fake_adapter
    _tech_mod.get_adapter = lambda: fake_adapter
    _fund_mod.get_adapter = lambda: fake_adapter
    _factor_mod.get_adapter = lambda: fake_adapter
    _health_mod.get_adapter = lambda: fake_adapter

    try:
        app = create_app()
        with TestClient(app) as tc:
            yield tc
    finally:
        # Restore
        _ad_mod._adapter = originals["_adapter"]
        _cache_mod._cache_manager = None
        for mod_name, mod in [
            ("market", _market_mod),
            ("financial", _fin_mod),
            ("technical", _tech_mod),
            ("fundamental", _fund_mod),
            ("factor", _factor_mod),
            ("health", _health_mod),
        ]:
            orig = originals[mod_name]
            if orig is not None:
                mod.get_adapter = orig
            else:
                delattr(mod, "get_adapter")
