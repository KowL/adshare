"""Unit tests for the realtime -> tushare field mapper."""

from datetime import datetime

import pytest

from adshare.services.realtime_tushare_mapper import (
    FREQ_TO_PERIOD,
    format_trade_time,
    kline_columns,
    kline_to_tushare_row,
    quote_columns,
    quote_to_tushare_row,
)


def _sample_snapshot() -> dict:
    payload = {
        "code": "600000.SH",
        "trade_time": "2026-07-16T14:32:15",
        "pre_close": 10.30,
        "last": 10.45,
        "open": 10.32,
        "high": 10.50,
        "low": 10.28,
        "close": 10.45,
        "volume": 52345678,
        "amount": 547832156.32,
        "num_trades": 12345,
        "high_limited": 11.33,
        "low_limited": 9.27,
    }
    for i in range(1, 6):
        payload[f"bid_price{i}"] = 10.44 - (i - 1) * 0.01
        payload[f"ask_price{i}"] = 10.45 + (i - 1) * 0.01
        payload[f"bid_volume{i}"] = 1000 * i
        payload[f"ask_volume{i}"] = 500 * i
    return payload


class TestQuoteColumns:
    def test_column_order(self):
        cols = quote_columns()
        assert cols[:10] == [
            "ts_code",
            "trade_time",
            "price",
            "open",
            "high",
            "low",
            "pre_close",
            "vol",
            "amount",
            "num_trades",
        ]
        assert cols[-2:] == ["high_limit", "low_limit"]
        # 5 档盘口: b{i}_p / a{i}_p / b{i}_v / a{i}_v
        for i in range(1, 6):
            for name in (f"b{i}_p", f"a{i}_p", f"b{i}_v", f"a{i}_v"):
                assert name in cols


class TestQuoteToTushareRow:
    def test_full_mapping(self):
        row = quote_to_tushare_row("600000.SH", _sample_snapshot())
        assert row["ts_code"] == "600000.SH"
        assert row["trade_time"] == "2026-07-16 14:32:15"
        assert row["price"] == 10.45
        assert row["open"] == 10.32
        assert row["high"] == 10.50
        assert row["low"] == 10.28
        assert row["pre_close"] == 10.30
        assert row["vol"] == 52345678
        assert row["amount"] == 547832156.32
        assert row["num_trades"] == 12345
        assert row["high_limit"] == 11.33
        assert row["low_limit"] == 9.27
        assert row["b1_p"] == 10.44
        assert row["a1_p"] == 10.45
        assert row["b1_v"] == 1000
        assert row["a1_v"] == 500
        assert row["b5_p"] == pytest.approx(10.40)
        assert row["a5_p"] == pytest.approx(10.49)
        assert row["b5_v"] == 5000
        assert row["a5_v"] == 2500

    def test_covers_all_columns(self):
        row = quote_to_tushare_row("600000.SH", _sample_snapshot())
        assert set(quote_columns()) == set(row.keys())

    def test_empty_payload(self):
        row = quote_to_tushare_row("600000.SH", {})
        assert row["ts_code"] == "600000.SH"
        assert row["price"] is None
        assert row["trade_time"] is None
        assert row["b1_p"] is None
        assert row["a5_v"] is None
        assert row["high_limit"] is None

    def test_none_payload(self):
        row = quote_to_tushare_row("600000.SH", None)
        assert row["ts_code"] == "600000.SH"
        assert row["price"] is None

    def test_code_fallback_to_payload(self):
        row = quote_to_tushare_row("", _sample_snapshot())
        assert row["ts_code"] == "600000.SH"

    def test_spaced_volume_keys_accepted(self):
        # SDK 手册中卷名字段排版为 ``ask _volume1`` 带空格，兼容该写法
        payload = _sample_snapshot()
        payload["ask _volume1"] = payload.pop("ask_volume1")
        payload["bid _volume3"] = payload.pop("bid_volume3")
        row = quote_to_tushare_row("600000.SH", payload)
        assert row["a1_v"] == 500
        assert row["b3_v"] == 3000


class TestKlineToTushareRow:
    def test_full_mapping(self):
        payload = {
            "code": "600000.SH",
            "kline_time": "2026-07-16T14:30:00",
            "open": 10.42,
            "high": 10.43,
            "low": 10.41,
            "close": 10.43,
            "volume": 12345,
            "amount": 12876543.0,
        }
        row = kline_to_tushare_row("600000.SH", "1MIN", payload)
        assert row == {
            "ts_code": "600000.SH",
            "trade_time": "2026-07-16 14:30:00",
            "freq": "1MIN",
            "open": 10.42,
            "high": 10.43,
            "low": 10.41,
            "close": 10.43,
            "vol": 12345,
            "amount": 12876543.0,
        }

    def test_covers_all_columns(self):
        row = kline_to_tushare_row("600000.SH", "5MIN", {})
        assert set(kline_columns()) == set(row.keys())
        assert row["freq"] == "5MIN"

    def test_freq_passthrough(self):
        for freq in FREQ_TO_PERIOD:
            row = kline_to_tushare_row("600000.SH", freq, {})
            assert row["freq"] == freq

    def test_trade_time_key_fallback(self):
        # 兼容 trade_time 命名（kline 主用 kline_time）
        row = kline_to_tushare_row(
            "600000.SH", "1MIN", {"trade_time": "2026-07-16T09:30:00"}
        )
        assert row["trade_time"] == "2026-07-16 09:30:00"


class TestFormatTradeTime:
    def test_iso_string(self):
        assert format_trade_time("2026-07-16T14:32:15") == "2026-07-16 14:32:15"

    def test_iso_string_with_space(self):
        assert format_trade_time("2026-07-16 14:32:15") == "2026-07-16 14:32:15"

    def test_datetime(self):
        dt = datetime(2026, 7, 16, 14, 32, 15)
        assert format_trade_time(dt) == "2026-07-16 14:32:15"

    def test_epoch_ms_int(self):
        ms = int(datetime(2026, 7, 16, 14, 32, 15).timestamp() * 1000)
        assert format_trade_time(ms) == "2026-07-16 14:32:15"
        assert format_trade_time(str(ms)) == "2026-07-16 14:32:15"

    def test_none(self):
        assert format_trade_time(None) is None
        assert format_trade_time("") is None

    def test_invalid_returns_none(self):
        assert format_trade_time("not-a-time") is None
