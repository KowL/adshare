"""Tests for factor analysis engine."""

import numpy as np
import pandas as pd
import pytest

from adshare.engines.factor.analysis import (
    preprocess_factor,
    detect_collinearity,
    composite_factors,
    build_factor_report_data,
)


@pytest.fixture
def sample_factor_data():
    """Generate sample factor and price data."""
    np.random.seed(42)
    n = 100
    stocks = [f"S{i:04d}" for i in range(20)]
    dates = pd.date_range("2024-01-01", periods=n, freq="B")

    factors = {
        "momentum": pd.DataFrame(np.random.randn(n, 20), index=dates, columns=stocks),
        "volatility": pd.DataFrame(np.random.randn(n, 20), index=dates, columns=stocks),
        "size": pd.DataFrame(np.random.randn(n, 20), index=dates, columns=stocks),
    }

    close = pd.DataFrame(
        100 + np.cumsum(np.random.randn(n, 20) * 0.5, axis=0),
        index=dates,
        columns=stocks,
    )

    benchmark = pd.DataFrame(
        {"close": 100 + np.cumsum(np.random.randn(n) * 0.3)}, index=dates
    )

    return factors, close, benchmark


class TestPreprocess:
    def test_preprocess_output(self, sample_factor_data):
        factors, _, _ = sample_factor_data
        result = preprocess_factor(factors["momentum"])
        assert result.shape == factors["momentum"].shape
        assert not result.isna().all().all()


class TestCollinearity:
    def test_detect_collinearity(self, sample_factor_data):
        factors, _, _ = sample_factor_data
        preprocessed = {k: preprocess_factor(v) for k, v in factors.items()}
        corr, vif, cond = detect_collinearity(preprocessed)
        assert cond > 0
        assert not vif.empty


class TestComposite:
    def test_composite_factors(self, sample_factor_data):
        factors, _, _ = sample_factor_data
        preprocessed = {k: preprocess_factor(v) for k, v in factors.items()}
        weights = {"momentum": 0.4, "volatility": 0.3, "size": 0.3}
        result = composite_factors(preprocessed, weights)
        assert result.shape == factors["momentum"].shape


class TestFactorReport:
    def test_build_report(self, sample_factor_data):
        factors, close, benchmark = sample_factor_data
        report = build_factor_report_data(
            factor_name="test",
            factor_raw=factors["momentum"],
            close_price=close,
            benchmark_df=benchmark,
            group_num=5,
            ic_decay=20,
        )
        assert "ic_result" in report
        assert "net_analysis" in report
