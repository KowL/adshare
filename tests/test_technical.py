"""Tests for technical indicators engine."""

import numpy as np
import pandas as pd
import pytest

from adshare.engines.technical.indicators import TechnicalIndicators


@pytest.fixture
def sample_data():
    """Generate sample OHLCV data."""
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n)) * 2
    low = close - np.abs(np.random.randn(n)) * 2
    open_p = close + np.random.randn(n) * 0.5
    volume = np.random.randint(1_000_000, 10_000_000, n)
    return pd.DataFrame(
        {"open": open_p, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


class TestMACD:
    def test_macd_output_keys(self, sample_data):
        ind = TechnicalIndicators()
        result = ind.MACD(sample_data)
        assert set(result.keys()) == {"DIF", "DEA", "MACD"}

    def test_macd_values(self, sample_data):
        ind = TechnicalIndicators()
        result = ind.MACD(sample_data)
        assert len(result["DIF"]) == len(sample_data)
        assert len(result["DEA"]) == len(sample_data)
        assert len(result["MACD"]) == len(sample_data)


class TestRSI:
    def test_rsi_output_keys(self, sample_data):
        ind = TechnicalIndicators()
        result = ind.RSI(sample_data)
        assert set(result.keys()) == {"RSI6", "RSI12", "RSI24"}

    def test_rsi_range(self, sample_data):
        ind = TechnicalIndicators()
        result = ind.RSI(sample_data)
        for key in result:
            valid = result[key]["close"].dropna()
            assert float(valid.min()) >= 0
            assert float(valid.max()) <= 100


class TestKDJ:
    def test_kdj_output_keys(self, sample_data):
        ind = TechnicalIndicators()
        result = ind.KDJ(sample_data["close"], sample_data["high"], sample_data["low"])
        assert set(result.keys()) == {"K", "D", "J"}


class TestBOLL:
    def test_boll_output_keys(self, sample_data):
        ind = TechnicalIndicators()
        result = ind.BOLL(sample_data)
        assert set(result.keys()) == {"BOLL", "UB", "LB"}

    def test_boll_band_width(self, sample_data):
        ind = TechnicalIndicators()
        result = ind.BOLL(sample_data)
        ub = result["UB"]["close"].dropna()
        lb = result["LB"]["close"].dropna()
        assert float((ub - lb).min()) >= 0


class TestAllIndicators:
    def test_indicator_list(self):
        ind = TechnicalIndicators()
        all_indicators = [m for m in dir(ind) if not m.startswith("_") and callable(getattr(ind, m, None))]
        assert len(all_indicators) == 56
