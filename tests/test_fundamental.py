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
        # Aliases required by various calc_* functions
        "TOT_SHARE", "INV", "ACCT_RECEIVABLE", "NOTES_RECEIVABLE",
        "TOTAL_CUR_LIAB", "ST_BORROWING", "LT_LOAN", "BONDS_PAYABLE",
        "NONCUR_LIAB_DUE_WITHIN_1Y", "TAX_PAYABLE",
    ]
    bs = pd.DataFrame({c: np.random.uniform(10, 500, n) for c in cols_bs})
    bs["REPORTING_PERIOD"] = quarters

    cols_inc = [
        "REVENUE", "OPERATING_PROFIT", "TOTAL_PROFIT", "NET_PRO_EXCL_MIN_INT_INC",
        "EPS", "TOTAL_SHARES", "OPERATING_COST", "SALES_EXPENSE", "ADMIN_EXPENSE",
        "FINANCIAL_EXPENSE", "RD_EXPENSE", "INCOME_TAX", "EBIT", "EBITDA",
        "DEPRECIATION", "AMORTIZATION",
        # Aliases required by various calc_* functions
        "OPERA_REV", "OPERA_PROFIT", "LESS_OPERA_COST",
        "LESS_FIN_EXP", "LESS_SELLING_EXP",
        "NET_PRO_AFTER_DED_NR_GL", "NON_OPER_INCOME", "NON_OPER_EXP",
    ]
    inc = pd.DataFrame({c: np.random.uniform(1, 100, n) for c in cols_inc})
    inc["REPORTING_PERIOD"] = quarters

    cols_cf = [
        "NET_CASH_FLOWS_OPERA_ACT", "NET_CASH_FLOWS_INV_ACT",
        "NET_CASH_FLOWS_FNC_ACT", "CAPEX", "FREE_CASH_FLOW",
        "NET_CASH_FLOW", "PAY_ALL_TAX",
        # Aliases required by calc_earnings_quality
        "END_BAL_CASH_CASH_EQU",
    ]
    cf = pd.DataFrame({c: np.random.uniform(-50, 50, n) for c in cols_cf})
    cf["REPORTING_PERIOD"] = quarters

    return bs, inc, cf


class TestProfitability:
    def test_profitability_keys(self, sample_financial_data):
        bs, inc, cf = sample_financial_data
        result = calc_profitability(bs, inc, cf)
        assert hasattr(result, 'keys') or hasattr(result, 'columns')


class TestGrowth:
    def test_growth_keys(self, sample_financial_data):
        from adshare.engines.fundamental.factors import calc_growth
        bs, inc, cf = sample_financial_data
        result = calc_growth(bs, inc, cf)
        assert hasattr(result, 'keys') or hasattr(result, 'columns')


class TestEfficiency:
    def test_efficiency_keys(self, sample_financial_data):
        from adshare.engines.fundamental.factors import calc_efficiency
        bs, inc, cf = sample_financial_data
        result = calc_efficiency(bs, inc, cf)
        assert hasattr(result, 'keys') or hasattr(result, 'columns')


class TestEarningsQuality:
    def test_quality_keys(self, sample_financial_data):
        from adshare.engines.fundamental.factors import calc_earnings_quality
        bs, inc, cf = sample_financial_data
        result = calc_earnings_quality(bs, inc, cf)
        assert hasattr(result, 'keys') or hasattr(result, 'columns')


class TestSafety:
    def test_safety_keys(self, sample_financial_data):
        from adshare.engines.fundamental.factors import calc_safety
        bs, inc, cf = sample_financial_data
        result = calc_safety(bs, inc, cf)
        assert hasattr(result, 'keys') or hasattr(result, 'columns')


class TestValuation:
    def test_valuation_exists(self):
        from adshare.engines.fundamental.factors import calc_valuation
        assert callable(calc_valuation)


def _empty_with_period():
    """Return an empty DataFrame with REPORTING_PERIOD column for _prep."""
    return pd.DataFrame(columns=["REPORTING_PERIOD"])


class TestEmptyData:
    def test_calc_profitability_empty(self):
        empty = _empty_with_period()
        result = calc_profitability(empty, empty, empty)
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_calc_growth_empty(self):
        from adshare.engines.fundamental.factors import calc_growth
        empty = _empty_with_period()
        result = calc_growth(empty, empty, empty)
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_calc_efficiency_empty(self):
        from adshare.engines.fundamental.factors import calc_efficiency
        empty = _empty_with_period()
        result = calc_efficiency(empty, empty, empty)
        assert isinstance(result, pd.DataFrame)
        assert result.empty
