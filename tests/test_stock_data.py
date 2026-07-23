"""Tests for Pro-style stock data API endpoints."""

from __future__ import annotations

import pytest

from adshare.services.dataframe_formatter import to_fields_items, build_response
from adshare.services.derived_metrics import (
    compute_price_changes,
    convert_volume_to_lots,
    map_kline_fields,
    map_stock_basic_fields,
    map_trade_cal_fields,
    filter_fields,
    apply_adjustment,
    compute_moving_averages,
    derive_suspensions,
)


# ------------------------------------------------------------------
# Formatter Tests
# ------------------------------------------------------------------

class TestFormatter:
    def test_empty_df(self):
        import pandas as pd
        result = to_fields_items(pd.DataFrame())
        assert result["fields"] == []
        assert result["items"] == []

    def test_basic_conversion(self):
        import pandas as pd
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        result = to_fields_items(df)
        assert result["fields"] == ["a", "b"]
        assert result["items"] == [[1, "x"], [2, "y"]]

    def test_field_map(self):
        import pandas as pd
        df = pd.DataFrame({"code": ["000001.SZ"], "date": [20250101]})
        result = to_fields_items(df, field_map={"code": "ts_code", "date": "trade_date"})
        assert result["fields"] == ["ts_code", "trade_date"]

    def test_build_response(self):
        result = build_response(data={"fields": ["a"], "items": [[1]]})
        assert result["code"] == 0
        assert result["msg"] == "success"
        assert result["data"]["fields"] == ["a"]


# ------------------------------------------------------------------
# Derived Metrics Tests
# ------------------------------------------------------------------

class TestComputePriceChanges:
    def test_basic(self):
        import pandas as pd
        df = pd.DataFrame({
            "date": [20250101, 20250102, 20250103],
            "close": [10.0, 11.0, 10.5],
        })
        result = compute_price_changes(df)
        # Descending order
        assert list(result["date"]) == [20250103, 20250102, 20250101]
        assert result["pre_close"].iloc[0] == 11.0  # 20250103 pre_close = 20250102 close
        assert result["change"].iloc[0] == -0.5
        assert result["pct_chg"].iloc[0] == -4.55  # -0.5/11.0*100 ≈ -4.55

    def test_empty(self):
        import pandas as pd
        result = compute_price_changes(pd.DataFrame())
        assert result.empty

    def test_previous_close_is_scoped_per_stock(self):
        import pandas as pd
        df = pd.DataFrame(
            {
                "code": ["000001.SZ", "600519.SH", "000001.SZ", "600519.SH"],
                "date": [20250102, 20250102, 20250103, 20250103],
                "close": [10.0, 100.0, 11.0, 101.0],
            }
        )

        result = compute_price_changes(df)
        latest = result[result["date"] == 20250103].set_index("code")

        assert latest.loc["000001.SZ", "pre_close"] == 10.0
        assert latest.loc["600519.SH", "pre_close"] == 100.0


class TestConvertVolume:
    def test_shares_to_lots(self):
        import pandas as pd
        df = pd.DataFrame({"volume": [150000, 200000]})
        result = convert_volume_to_lots(df)
        assert list(result["vol"]) == [1500, 2000]


class TestMapStockBasic:
    def test_basic(self):
        import pandas as pd
        df = pd.DataFrame({
            "code": ["000001.SZ", "600519.SH"],
            "name": ["平安银行", "贵州茅台"],
            "board": ["主板", "主板"],
            "is_listed": [True, True],
            "list_date": [19910403, 20010827],
        })
        result = map_stock_basic_fields(df)
        assert list(result["ts_code"]) == ["000001.SZ", "600519.SH"]
        assert list(result["symbol"]) == ["000001", "600519"]
        assert list(result["exchange"]) == ["SZSE", "SSE"]
        assert list(result["list_status"]) == ["L", "L"]

    def test_empty(self):
        import pandas as pd
        result = map_stock_basic_fields(pd.DataFrame())
        assert "ts_code" in result.columns
        assert result.empty


class TestMapTradeCal:
    def test_basic(self):
        import pandas as pd
        df = pd.DataFrame({
            "date": [20250101, 20250102, 20250103],
            "market": ["SH", "SH", "SH"],
            "is_trading_day": [False, True, True],
        })
        result = map_trade_cal_fields(df)
        assert "cal_date" in result.columns
        assert "is_open" in result.columns
        assert "pretrade_date" in result.columns
        # 20250102 is_open=1, pretrade_date should be None (first trading day)
        # 20250103 is_open=1, pretrade_date should be 20250102
        row_20250103 = result[result["cal_date"] == 20250103].iloc[0]
        assert row_20250103["pretrade_date"] == 20250102


class TestMapKline:
    def test_rename(self):
        import pandas as pd
        df = pd.DataFrame({
            "code": ["000001.SZ"],
            "date": [20250101],
            "open": [10.0],
            "close": [11.0],
        })
        result = map_kline_fields(df)
        assert "ts_code" in result.columns
        assert "trade_date" in result.columns
        assert result["ts_code"].iloc[0] == "000001.SZ"


class TestFilterFields:
    def test_filter(self):
        import pandas as pd
        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        result = filter_fields(df, "a,c")
        assert list(result.columns) == ["a", "c"]

    def test_no_filter(self):
        import pandas as pd
        df = pd.DataFrame({"a": [1], "b": [2]})
        result = filter_fields(df, None)
        assert list(result.columns) == ["a", "b"]


class TestApplyAdjustment:
    def test_qfq(self):
        import pandas as pd
        df = pd.DataFrame({
            "date": [20250101, 20250102, 20250103],
            "open": [10.0, 11.0, 12.0],
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.5, 11.5, 12.5],
        })
        adj_df = pd.DataFrame({
            "date": [20250101, 20250102, 20250103],
            "adj_factor": [1.0, 1.1, 1.2],
        })
        result = apply_adjustment(df, adj_df, "qfq")
        # Latest date (20250103) factor 1.2 should be base
        # 20250103 close = 12.5 * 1.2 / 1.2 = 12.5
        assert result["close"].iloc[2] == 12.5
        # 20250101 close = 10.5 * 1.0 / 1.2 = 8.75
        assert result["close"].iloc[0] == 8.75

    def test_hfq(self):
        import pandas as pd
        df = pd.DataFrame({
            "date": [20250101, 20250102],
            "open": [10.0, 11.0],
            "high": [11.0, 12.0],
            "low": [9.0, 10.0],
            "close": [10.5, 11.5],
        })
        adj_df = pd.DataFrame({
            "date": [20250101, 20250102],
            "adj_factor": [1.0, 1.1],
        })
        result = apply_adjustment(df, adj_df, "hfq")
        assert result["close"].iloc[0] == 10.5  # 10.5 * 1.0
        assert result["close"].iloc[1] == 12.65  # 11.5 * 1.1 = 12.65


class TestComputeMA:
    def test_ma(self):
        import pandas as pd
        df = pd.DataFrame({
            "date": [20250101, 20250102, 20250103, 20250104, 20250105],
            "close": [10.0, 11.0, 12.0, 13.0, 14.0],
        })
        result = compute_moving_averages(df, [2, 3])
        # Descending order
        assert result["date"].iloc[0] == 20250105
        # ma2 for 20250105 = (14+13)/2 = 13.5
        assert result["ma2"].iloc[0] == 13.5


class TestDeriveSuspensions:
    def test_basic(self):
        import pandas as pd
        df = pd.DataFrame({
            "code": ["000001.SZ"] * 5,
            "date": [20250101, 20250102, 20250103, 20250104, 20250105],
            "volume": [1000, 0, 0, 2000, 3000],
        })
        result = derive_suspensions(df)
        assert len(result) == 1
        assert result.iloc[0]["ts_code"] == "000001.SZ"
        assert result.iloc[0]["suspend_date"] == 20250102
        assert result.iloc[0]["resume_date"] == 20250104

    def test_no_suspend(self):
        import pandas as pd
        df = pd.DataFrame({
            "code": ["000001.SZ"] * 3,
            "date": [20250101, 20250102, 20250103],
            "volume": [1000, 2000, 3000],
        })
        result = derive_suspensions(df)
        assert result.empty


# ------------------------------------------------------------------
# Router Import Test
# ------------------------------------------------------------------

class TestRouterImport:
    def test_router_imports(self):
        from adshare.routers import stock_data
        assert stock_data.router is not None



class TestBuildLimitList:
    def test_up_and_down(self):
        from adshare.services.derived_metrics import build_limit_list

        class FakeItem:
            def __init__(self, code, name, price, change_pct, board, volume, amount, pre_close, limit_up_days=1):
                self.code = code
                self.name = name
                self.price = price
                self.changePct = change_pct
                self.amplitude = 0.05
                self.board = board
                self.volume = volume
                self.amount = amount
                self.preClose = pre_close
                self.firstTime = "09:35:00"
                self.finalTime = "14:55:00"
                self.limitUpDays = limit_up_days

        up = [FakeItem("000001", "平安银行", 11.0, 0.1, "主板", 10000, 110000, 10.0)]
        down = [FakeItem("300001", "特锐德", 18.0, -0.2, "创业板", 5000, 90000, 22.5)]
        df = build_limit_list(up, down, 20250611)
        assert len(df) == 2
        assert df.iloc[0]["ts_code"] == "000001.SZ"
        assert df.iloc[0]["limit"] == "U"
        assert df.iloc[1]["ts_code"] == "300001.SZ"
        assert df.iloc[1]["limit"] == "D"
        assert df.iloc[1]["pct_chg"] == -20.0


class TestFilterNewShares:
    def test_filter(self):
        from adshare.services.derived_metrics import filter_new_shares
        import pandas as pd
        df = pd.DataFrame({
            "ts_code": ["000001.SZ", "000002.SZ", "688001.SH"],
            "symbol": ["000001", "000002", "688001"],
            "name": ["A", "B", "C"],
            "list_date": [19910403, 20250601, 20250610],
        })
        result = filter_new_shares(df, 20250601)
        assert len(result) == 2
        assert list(result["ts_code"]) == ["000002.SZ", "688001.SH"]
