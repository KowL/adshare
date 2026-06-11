"""Tests for limit-down and related market activity services."""

import pandas as pd

from adshare.services.limit_up import (
    LimitDownService,
    MarketActivityService,
    StrongStockPoolService,
)


class FakeLimitDownAdapter:
    def __init__(self):
        self.is_logged_in = True

    def get_code_info(self):
        return pd.DataFrame({
            "code": ["000001.SZ", "600000.SH", "300001.SZ"],
            "name": ["平安银行", "浦发银行", "创业科技"],
            "board": ["主板", "主板", "创业板"],
            "is_listed": [True, True, True],
        })

    def get_kline(self, codes, begin_date, end_date, period="day", **kwargs):
        code_list = [c.strip() for c in codes.split(",")]
        rows = []
        payload = {
            "000001.SZ": [(20240606, 11.0), (20240607, 10.0)],
            "600000.SH": [(20240606, 10.0), (20240607, 9.0)],
            "300001.SZ": [(20240606, 24.0), (20240607, 20.0)],
        }
        for code in code_list:
            for date, close in payload.get(code, []):
                rows.append({
                    "code": code,
                    "date": date,
                    "open": close + 0.2,
                    "high": close + 0.5,
                    "low": close - 0.2,
                    "close": close,
                    "volume": 100,
                    "amount": close * 100,
                })
        return pd.DataFrame(rows)


class FakeLimitDownWarehouse:
    def __init__(self, codes_df, kline_df):
        self._codes = codes_df
        self._kline = kline_df

    def query_codes(self, **kwargs):
        df = self._codes.copy()
        if "is_listed" in kwargs and kwargs["is_listed"] is not None and "is_listed" in df.columns:
            df = df[df["is_listed"] == kwargs["is_listed"]]
        return df

    def query_kline(self, codes, begin_date, end_date, period="day"):
        if self._kline.empty:
            return pd.DataFrame()
        df = self._kline.copy()
        df = df[df["code"].isin(codes)]
        df = df[(df["date"] >= begin_date) & (df["date"] <= end_date)]
        return df.reset_index(drop=True)


class TestLimitDownService:
    def test_limit_down_from_local_data(self):
        adapter = FakeLimitDownAdapter()
        warehouse = FakeLimitDownWarehouse(
            adapter.get_code_info(),
            adapter.get_kline("000001.SZ,600000.SH,300001.SZ", 20240601, 20240607),
        )
        service = LimitDownService(adapter=adapter, warehouse=warehouse)

        result = service.get_limit_down(date=20240607, exclude_st=False)

        codes = {stock.code for stock in result.stocks}
        # 600000: 10.0->9.0 = -10% (主板跌停 10.0*0.9=9.0)
        # 000001: 11.0->10.0 = -9.09% (跌停价 9.9, 未跌停)
        # 300001: 24.0->20.0 = -16.7% (创业板跌停 24.0*0.8=19.2, 未跌停)
        assert codes == {"600000"}

    def test_limit_down_empty_warehouse(self):
        service = LimitDownService(warehouse=False)
        result = service.get_limit_down(date=20240607)
        assert result.stocks == []
        assert result.count == 0


class TestMarketActivityService:
    def test_market_activity_from_local_data(self):
        adapter = FakeLimitDownAdapter()
        kline = adapter.get_kline("000001.SZ,600000.SH", 20240601, 20240607)
        warehouse = FakeLimitDownWarehouse(adapter.get_code_info(), kline)
        service = MarketActivityService(adapter=adapter, warehouse=warehouse)

        result = service.get_market_activity(date=20240607)

        assert result.count > 0
        assert result.distribution.total > 0

    def test_market_activity_empty_warehouse(self):
        service = MarketActivityService(warehouse=False)
        result = service.get_market_activity(date=20240607)
        assert result.count == 0


class TestStrongStockPoolService:
    def test_strong_pool_from_local_data(self):
        adapter = FakeLimitDownAdapter()
        kline = adapter.get_kline("000001.SZ,600000.SH", 20240601, 20240607)
        warehouse = FakeLimitDownWarehouse(adapter.get_code_info(), kline)
        service = StrongStockPoolService(adapter=adapter, warehouse=warehouse)

        result = service.get_strong_pool(date=20240607)

        assert isinstance(result.stocks, list)

    def test_strong_pool_empty_warehouse(self):
        service = StrongStockPoolService(warehouse=False)
        result = service.get_strong_pool(date=20240607)
        assert result.stocks == []
        assert result.count == 0
