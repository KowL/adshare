"""Tests for fundamental factors engine."""

import numpy as np
import pandas as pd
import pytest

from adshare.engines.fundamental.factors import (
    calc_profitability,
    calc_valuation,
    calc_growth,
)


@pytest.fixture
def sample_financial_data():
    """Generate sample financial statement data."""
    np.random.seed(42)
    n = 20
    quarters = []
    for y in range(2020, 2025):
        for q in ["0331", "0630", "0930", "1231"]:
            quarters.append(str(y) + q)
    quarters = quarters[:n]

    cols_bs = [
        "TOTAL_ASSETS", "TOTAL_LIABILITIES", "NET_ASSETS",
        "TOT_SHARE_EQUITY_EXCL_MIN_INT", "TOT_SHARE_EQUITY_INCL_MIN_INT",
        "GOODWILL", "INTANGIBLE_ASSETS", "FIXED_ASSETS", "INVENTORY",
        "ACCOUNTS_RECEIVABLE", "CASH", "SHORT_TERM_LOANS", "LONG_TERM_LOANS",
    ]
    bs = pd.DataFrame({c: np.random.uniform(10, 500, n) for c in cols_bs})
    bs["REPORTING_PERIOD"] = quarters

    cols_inc = [
        "REVENUE", "OPERATING_PROFIT", "TOTAL_PROFIT", "NET_PRO_EXCL_MIN_INT_INC",
        "EPS", "TOTAL_SHARES", "OPERATING_COST", "SALES_EXPENSE", "ADMIN_EXPENSE",
        "FINANCIAL_EXPENSE", "RD_EXPENSE", "INCOME_TAX", "EBIT", "EBITDA",
        "DEPRECIATION", "AMORTIZATION",
    ]
    inc = pd.DataFrame({c: np.random.uniform(1, 100, n) for c in cols_inc})
    inc["REPORTING_PERIOD"] = quarters

    cols_cf = [
        "NET_CASH_FLOWS_OPERA_ACT", "NET_CASH_FLOWS_INV_ACT",
        "NET_CASH_FLOWS_FNC_ACT", "CAPEX", "FREE_CASH_FLOW",
        "NET_CASH_FLOW", "PAY_ALL_TAX",
    ]
    cf = pd.DataFrame({c: np.random.uniform(-50, 50, n) for c in cols_cf})
    cf["REPORTING_PERIOD"] = quarters

    return bs, inc, cf


class TestProfitability:
    def test_profitability_keys(self, sample_financial_data):
        bs, inc, cf = sample_financial_data
        result = calc_profitability(bs, inc, cf)
        assert hasattr(result, 'keys') or hasattr(result, 'columns')


class TestValuation:
    def test_valuation_exists(self):
        from adshare.engines.fundamental.factors import calc_valuation
        assert callable(calc_valuation)
