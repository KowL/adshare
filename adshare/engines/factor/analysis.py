"""Factor analysis engine.

Implements factor preprocessing, IC analysis, regression analysis,
stratification analysis, and crowding analysis.
All in pure pandas/numpy without AmazingData dependency.
"""

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats


# ============================================================
#  Preprocessing
# ============================================================

class FactorPreProcessing:
    """Factor preprocessing: extreme value processing, scaling, fill NaN."""

    def __init__(self, factor_raw: pd.DataFrame):
        self.raw_data = factor_raw.copy()
        self.processed_data = factor_raw.copy()

    def extreme_processing(self, method: str = "mad", median_multiple: float = 5.0):
        """MAD extreme value processing."""
        for col in self.processed_data.columns:
            series = self.processed_data[col]
            median = series.median()
            mad = (series - median).abs().median()
            upper = median + median_multiple * mad
            lower = median - median_multiple * mad
            self.processed_data[col] = series.clip(lower=lower, upper=upper)
        return self

    def scale_processing(self, method: str = "z_score"):
        """Z-Score standardization."""
        if method == "z_score":
            self.processed_data = (
                self.processed_data - self.processed_data.mean()
            ) / self.processed_data.std()
        elif method == "min_max":
            self.processed_data = (
                self.processed_data - self.processed_data.min()
            ) / (self.processed_data.max() - self.processed_data.min())
        return self

    def fill_nan_processing(self, method: str = "median"):
        """Fill NaN values."""
        if method == "median":
            self.processed_data = self.processed_data.fillna(self.processed_data.median())
        elif method == "mean":
            self.processed_data = self.processed_data.fillna(self.processed_data.mean())
        elif method == "zero":
            self.processed_data = self.processed_data.fillna(0)
        return self


# ============================================================
#  IC Analysis
# ============================================================

class IcAnalysis:
    """Information Coefficient analysis."""

    def __init__(
        self,
        factor: pd.DataFrame,
        factor_name: str,
        close_price: pd.DataFrame,
        ic_decay: int = 20,
    ):
        self.factor = factor
        self.factor_name = factor_name
        self.close_price = close_price
        self.ic_decay = ic_decay
        self.ic_df: Optional[pd.DataFrame] = None
        self.ic_result: Optional[pd.DataFrame] = None
        self.p_value_df: Optional[pd.DataFrame] = None

    def cal_ic_df(self, method: str = "spearmanr"):
        """Calculate IC time series."""
        # Align factor and close price
        common_idx = self.factor.index.intersection(self.close_price.index)
        common_cols = self.factor.columns.intersection(self.close_price.columns)

        f = self.factor.loc[common_idx, common_cols]
        close = self.close_price.loc[common_idx, common_cols]

        # Calculate forward returns (next period return)
        fwd_returns = close.pct_change(1).shift(-1)

        ic_records = []
        pvalue_records = []

        for date in common_idx:
            f_row = f.loc[date].dropna()
            r_row = fwd_returns.loc[date].reindex(f_row.index).dropna()

            if len(f_row) < 10 or len(r_row) < 10:
                continue

            if method == "spearmanr":
                corr, pval = stats.spearmanr(f_row, r_row)
            else:
                corr, pval = stats.pearsonr(f_row, r_row)

            ic_records.append({"date": date, "ic": corr, "p_value": pval})

        if ic_records:
            self.ic_df = pd.DataFrame(ic_records).set_index("date")
            self.p_value_df = self.ic_df[["p_value"]].copy()
            self.ic_df = self.ic_df[["ic"]]
        else:
            self.ic_df = pd.DataFrame(columns=["ic"])
            self.p_value_df = pd.DataFrame(columns=["p_value"])

        return self

    def cal_ic_indicator(self):
        """Calculate IC summary statistics."""
        if self.ic_df is None or self.ic_df.empty:
            self.ic_result = pd.DataFrame(
                {
                    "delay_1": [np.nan, np.nan, np.nan, np.nan, np.nan],
                },
                index=["IC 均值", "IC 标准差", "IC IR", "IC > 0 占比", "t统计量"],
            )
            return self

        ic_mean = self.ic_df["ic"].mean()
        ic_std = self.ic_df["ic"].std()
        ic_ir = ic_mean / ic_std if ic_std != 0 else np.nan
        ic_positive_ratio = (self.ic_df["ic"] > 0).mean()
        t_stat = ic_mean / (ic_std / np.sqrt(len(self.ic_df))) if ic_std != 0 else np.nan

        self.ic_result = pd.DataFrame(
            {
                "delay_1": [ic_mean, ic_std, ic_ir, ic_positive_ratio, t_stat],
            },
            index=["IC 均值", "IC 标准差", "IC IR", "IC > 0 占比", "t统计量"],
        )
        return self


# ============================================================
#  Regression Analysis
# ============================================================

class RegressionAnalysis:
    """Factor return regression analysis."""

    def __init__(
        self,
        factor: pd.DataFrame,
        factor_name: str,
        close_price: pd.DataFrame,
        benchmark_df: pd.DataFrame,
    ):
        self.factor = factor
        self.factor_name = factor_name
        self.close_price = close_price
        self.benchmark_df = benchmark_df
        self.factor_return: Optional[pd.DataFrame] = None
        self.factor_t_value: Optional[pd.DataFrame] = None
        self.net_analysis_result: Optional[Dict] = None

    def cal_factor_return(self):
        """Calculate factor return via cross-sectional regression."""
        common_idx = self.factor.index.intersection(self.close_price.index)
        common_cols = self.factor.columns.intersection(self.close_price.columns)

        f = self.factor.loc[common_idx, common_cols]
        close = self.close_price.loc[common_idx, common_cols]
        fwd_returns = close.pct_change(1).shift(-1)

        returns = []
        for date in common_idx:
            f_row = f.loc[date].dropna()
            r_row = fwd_returns.loc[date].reindex(f_row.index).dropna()

            if len(f_row) < 10:
                continue

            # Simple regression: return ~ factor
            X = np.column_stack([np.ones(len(f_row)), f_row.values])
            y = r_row.values
            try:
                beta = np.linalg.lstsq(X, y, rcond=None)[0]
                returns.append({"date": date, "factor_return": beta[1]})
            except Exception:
                continue

        if returns:
            self.factor_return = pd.DataFrame(returns).set_index("date")
        else:
            self.factor_return = pd.DataFrame(columns=["factor_return"])

        return self

    def cal_t_value_statistics(self):
        """Calculate t-value statistics."""
        if self.factor_return is None or self.factor_return.empty:
            self.factor_t_value = pd.DataFrame(columns=["t_value"])
            return self

        mean_ret = self.factor_return["factor_return"].mean()
        std_ret = self.factor_return["factor_return"].std()
        n = len(self.factor_return)
        t_val = mean_ret / (std_ret / np.sqrt(n)) if std_ret != 0 else np.nan

        self.factor_t_value = pd.DataFrame(
            {"t_value": [t_val]}, index=[self.factor_name]
        )
        return self

    def cal_net_analysis(self):
        """Calculate net value analysis."""
        if self.factor_return is None or self.factor_return.empty:
            self.net_analysis_result = {
                "cumprod": {
                    "annual_return": 0,
                    "sharpe_ratio": 0,
                    "max_drawdown": 0,
                }
            }
            return self

        ret = self.factor_return["factor_return"]
        cum_ret = (1 + ret).cumprod()

        # Annual return
        total_ret = cum_ret.iloc[-1] - 1
        years = len(ret) / 252
        annual_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0

        # Sharpe ratio
        sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() != 0 else 0

        # Max drawdown
        rolling_max = cum_ret.cummax()
        drawdown = (cum_ret - rolling_max) / rolling_max
        max_dd = drawdown.min()

        self.net_analysis_result = {
            "cumprod": {
                "annual_return": annual_ret,
                "sharpe_ratio": sharpe,
                "max_drawdown": max_dd * 100,  # percentage
            }
        }
        return self

    def cal_acf(self, nlags: int = 10):
        """Calculate autocorrelation function."""
        if self.factor_return is None or self.factor_return.empty:
            return pd.DataFrame()
        from statsmodels.tsa.stattools import acf

        acf_vals = acf(self.factor_return["factor_return"].dropna(), nlags=nlags)
        return pd.DataFrame({"acf": acf_vals})


# ============================================================
#  Stratification Analysis
# ============================================================

class StratificationAnalysis:
    """Stratification (grouping) analysis."""

    def __init__(
        self,
        factor: pd.DataFrame,
        close_price: pd.DataFrame,
        group_num: int = 5,
        ascending: bool = False,
        benchmark_series: Optional[pd.Series] = None,
    ):
        self.factor = factor
        self.close_price = close_price
        self.group_num = group_num
        self.ascending = ascending
        self.benchmark_series = benchmark_series
        self.group_navs: Optional[pd.DataFrame] = None
        self.group_metrics: Optional[Dict] = None
        self.turnover: Optional[pd.DataFrame] = None
        self.signal_decay: Optional[pd.DataFrame] = None
        self.signal_reversal: Optional[pd.DataFrame] = None
        self.long_short_nav: Optional[pd.Series] = None
        self.group_keys: List[str] = []

    def run(self):
        """Run stratification backtest."""
        common_idx = self.factor.index.intersection(self.close_price.index)
        common_cols = self.factor.columns.intersection(self.close_price.columns)

        f = self.factor.loc[common_idx, common_cols]
        close = self.close_price.loc[common_idx, common_cols]
        fwd_returns = close.pct_change(1).shift(-1)

        group_navs = {f"group_{i}": [] for i in range(self.group_num)}
        group_dates = []

        for date in common_idx:
            f_row = f.loc[date].dropna()
            r_row = fwd_returns.loc[date].reindex(f_row.index).dropna()

            if len(f_row) < self.group_num * 5:
                continue

            # Rank and group
            ranks = f_row.rank(ascending=self.ascending)
            group_size = len(ranks) // self.group_num

            group_rets = []
            for i in range(self.group_num):
                if i == self.group_num - 1:
                    mask = (ranks > i * group_size)
                else:
                    mask = (ranks > i * group_size) & (ranks <= (i + 1) * group_size)
                group_stocks = ranks[mask].index
                group_ret = r_row.reindex(group_stocks).mean()
                group_rets.append(group_ret)
                group_navs[f"group_{i}"].append(group_ret)

            group_dates.append(date)

        if group_dates:
            self.group_navs = pd.DataFrame(group_navs, index=group_dates)
            self.group_keys = [f"group_{i}" for i in range(self.group_num)]

            # Calculate metrics
            self.group_metrics = {}
            for key in self.group_keys:
                ret = self.group_navs[key]
                cum_ret = (1 + ret).cumprod()
                total_ret = cum_ret.iloc[-1] - 1
                years = len(ret) / 252
                annual_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
                sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() != 0 else 0
                rolling_max = cum_ret.cummax()
                drawdown = (cum_ret - rolling_max) / rolling_max
                max_dd = drawdown.min()

                self.group_metrics[key] = {
                    "annual_return": annual_ret,
                    "sharpe_ratio": sharpe,
                    "max_drawdown": max_dd * 100,
                }

            # Long-short NAV
            self.long_short_nav = (
                self.group_navs[self.group_keys[0]] - self.group_navs[self.group_keys[-1]]
            )

        return self


# ============================================================
#  Multi-Factor Analysis
# ============================================================

def preprocess_factor(factor_raw: pd.DataFrame) -> pd.DataFrame:
    """Preprocess a single factor."""
    fpp = FactorPreProcessing(factor_raw)
    fpp.extreme_processing()
    fpp.scale_processing()
    fpp.fill_nan_processing()
    return fpp.processed_data


def detect_collinearity(factors: Dict[str, pd.DataFrame]) -> Tuple[pd.DataFrame, pd.DataFrame, float]:
    """Detect collinearity among factors."""
    factor_names = list(factors.keys())
    cross_section = {}
    for name, f in factors.items():
        last_valid = f.dropna(how="all").index[-1]
        cross_section[name] = f.loc[last_valid].dropna()

    common_stocks = cross_section[factor_names[0]].index
    for name in factor_names[1:]:
        common_stocks = common_stocks.intersection(cross_section[name].index)

    cross_data = pd.DataFrame(
        {name: cross_section[name].loc[common_stocks].values for name in factor_names}
    )

    corr_matrix = cross_data.corr()

    # VIF calculation
    vif_values = {}
    for i, name in enumerate(factor_names):
        y = cross_data.iloc[:, i].values
        X = cross_data.drop(columns=[name]).values
        if X.shape[1] == 0:
            vif_values[name] = 1.0
            continue
        X_design = np.column_stack([np.ones(len(y)), X])
        try:
            beta = np.linalg.lstsq(X_design, y, rcond=None)[0]
            y_pred = X_design @ beta
            ss_res = np.sum((y - y_pred) ** 2)
            ss_tot = np.sum((y - np.mean(y)) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot > 1e-12 else 0
            vif_values[name] = 1 / (1 - r2) if r2 < 1 - 1e-12 else float("inf")
        except Exception:
            vif_values[name] = 1.0

    vif_df = pd.DataFrame(
        {"因子": list(vif_values.keys()), "VIF": list(vif_values.values())}
    ).set_index("因子")

    cond_num = float(np.linalg.cond(cross_data.values))

    return corr_matrix, vif_df, cond_num


def orthogonalize_factors(
    factors: Dict[str, pd.DataFrame], method: str = "symmetric"
) -> Dict[str, pd.DataFrame]:
    """Orthogonalize factors using Gram-Schmidt."""
    factor_names = list(factors.keys())

    # Align indices
    common_idx = factors[factor_names[0]].index
    common_cols = factors[factor_names[0]].columns
    for name in factor_names[1:]:
        common_idx = common_idx.intersection(factors[name].index)
        common_cols = common_cols.intersection(factors[name].columns)

    aligned = {name: factors[name].loc[common_idx, common_cols] for name in factor_names}

    orthogonalized = {}
    for name in factor_names:
        orthogonalized[name] = pd.DataFrame(np.nan, index=common_idx, columns=common_cols)

    for t in common_idx:
        X = np.column_stack([aligned[name].loc[t].values for name in factor_names])
        Q = np.zeros_like(X)
        for i in range(X.shape[1]):
            v = X[:, i].copy().astype(float)
            for j in range(i):
                v -= np.dot(Q[:, j], X[:, i]) * Q[:, j]
            norm = np.linalg.norm(v)
            if norm > 1e-12:
                Q[:, i] = v / norm

        for i, name in enumerate(factor_names):
            orthogonalized[name].loc[t] = Q[:, i]

    return orthogonalized


def composite_factors(
    factors: Dict[str, pd.DataFrame],
    weights: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """Composite multiple factors into a single factor."""
    factor_names = list(factors.keys())

    if weights is None:
        weights = {name: 1.0 / len(factor_names) for name in factor_names}

    # Align
    common_idx = factors[factor_names[0]].index
    common_cols = factors[factor_names[0]].columns
    for name in factor_names[1:]:
        common_idx = common_idx.intersection(factors[name].index)
        common_cols = common_cols.intersection(factors[name].columns)

    composite = pd.DataFrame(0.0, index=common_idx, columns=common_cols)
    for name in factor_names:
        composite += factors[name].loc[common_idx, common_cols] * weights.get(name, 0)

    return composite


# ============================================================
#  Report Data Builder
# ============================================================

def build_factor_report_data(
    factor_name: str,
    factor_raw: pd.DataFrame,
    close_price: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    market_cap: Optional[pd.DataFrame] = None,
    group_num: int = 5,
    ic_decay: int = 20,
) -> Dict:
    """Build complete factor analysis report data."""
    # Preprocess
    factor = preprocess_factor(factor_raw)

    # IC Analysis
    ia = IcAnalysis(factor, factor_name, close_price, ic_decay)
    ia.cal_ic_df()
    ia.cal_ic_indicator()

    # Regression Analysis
    ra = RegressionAnalysis(factor, factor_name, close_price, benchmark_df)
    ra.cal_factor_return()
    ra.cal_t_value_statistics()
    ra.cal_net_analysis()

    # Stratification Analysis
    bm_series = (
        benchmark_df["close"] / benchmark_df["close"].iloc[0]
        if not benchmark_df.empty
        else None
    )
    sa = StratificationAnalysis(factor, close_price, group_num, False, bm_series)
    sa.run()

    return {
        "factor_name": factor_name,
        "ic_df": ia.ic_df,
        "ic_result": ia.ic_result,
        "p_value_df": ia.p_value_df,
        "factor_return": ra.factor_return,
        "factor_t_value": ra.factor_t_value,
        "net_analysis": ra.net_analysis_result,
        "group_navs": sa.group_navs,
        "group_metrics": sa.group_metrics,
        "long_short_nav": sa.long_short_nav,
        "group_keys": sa.group_keys,
    }
