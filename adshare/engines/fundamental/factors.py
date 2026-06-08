"""90 Fundamental Indicators Engine.

Ported from xysz run_fundamental_analysis.py.
All indicators implemented in pure pandas/numpy without AmazingData dependency.
"""

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ================================================================
#  Helper Functions
# ================================================================

def safe_div(a, b):
    """Safe division, return NaN when denominator is 0 or NaN."""
    a = pd.Series(a).values.astype(float) if not isinstance(a, np.ndarray) else a.astype(float)
    b = pd.Series(b).values.astype(float) if not isinstance(b, np.ndarray) else b.astype(float)
    with np.errstate(divide='ignore', invalid='ignore'):
        result = np.where((b == 0) | np.isnan(b) | np.isnan(a), np.nan, a / b)
    return result


def get_ttm(df, field):
    """Calculate TTM (trailing 12 months) cumulative value."""
    if df is None or df.empty or field not in df.columns:
        return pd.Series(dtype=float)
    df = df.sort_values('REPORTING_PERIOD').reset_index(drop=True)
    rp = df['REPORTING_PERIOD'].astype(str)
    result = pd.Series(np.nan, index=df.index)
    for i in range(len(df)):
        val = df[field].iloc[i]
        if pd.isna(val):
            continue
        rp_str = rp.iloc[i]
        yr = rp_str[:4]
        mmdd = rp_str[4:]
        if mmdd == '1231':
            result.iloc[i] = val
        else:
            prev_yr = str(int(yr) - 1)
            ann_mask = rp == prev_yr + '1231'
            same_mask = rp == prev_yr + mmdd
            if ann_mask.any() and same_mask.any():
                ann_val = df.loc[ann_mask, field].iloc[-1]
                same_val = df.loc[same_mask, field].iloc[-1]
                if pd.notna(ann_val) and pd.notna(same_val):
                    result.iloc[i] = val + ann_val - same_val
    return result


def get_single_quarter(df, field):
    """Calculate single quarter value from cumulative data."""
    if df is None or df.empty or field not in df.columns:
        return pd.Series(dtype=float)
    df = df.sort_values('REPORTING_PERIOD').reset_index(drop=True)
    rp = df['REPORTING_PERIOD'].astype(str)
    result = pd.Series(np.nan, index=df.index)
    prev_map = {'0630': '0331', '0930': '0630', '1231': '0930'}
    for i in range(len(df)):
        val = df[field].iloc[i]
        rp_str = rp.iloc[i]
        mmdd = rp_str[4:]
        if mmdd == '0331':
            result.iloc[i] = val
        elif mmdd in prev_map and pd.notna(val):
            yr = rp_str[:4]
            prev_rp = yr + prev_map[mmdd]
            prev_mask = rp == prev_rp
            if prev_mask.any():
                prev_val = df.loc[prev_mask, field].iloc[-1]
                if pd.notna(prev_val):
                    result.iloc[i] = val - prev_val
    return result


def _yoy(s):
    """Year-over-year growth rate."""
    if not isinstance(s.index, pd.Index) or s.empty:
        return s
    rp = s.index.astype(str)
    result = pd.Series(np.nan, index=s.index)
    rp_to_val = dict(zip(rp, s.values))
    for i, rp_str in enumerate(rp):
        if len(rp_str) < 8:
            continue
        try:
            prev_rp = str(int(rp_str[:4]) - 1) + rp_str[4:]
        except (ValueError, IndexError):
            continue
        if prev_rp in rp_to_val:
            cur = s.values[i]
            prev = rp_to_val[prev_rp]
            if pd.notna(cur) and pd.notna(prev) and prev != 0:
                result.iloc[i] = (cur - prev) / abs(prev)
    return result


def _qoq(s):
    """Quarter-over-quarter growth rate."""
    if not isinstance(s.index, pd.Index) or s.empty:
        return s
    prev_map = {'0331': '1231', '0630': '0331', '0930': '0630', '1231': '0930'}
    rp = s.index.astype(str)
    result = pd.Series(np.nan, index=s.index)
    rp_to_val = dict(zip(rp, s.values))
    for i, rp_str in enumerate(rp):
        if len(rp_str) < 8:
            continue
        mmdd = rp_str[4:]
        if mmdd not in prev_map:
            continue
        yr = rp_str[:4]
        prev_mmdd = prev_map[mmdd]
        prev_yr = str(int(yr) - 1) if mmdd == '0331' else yr
        prev_rp = prev_yr + prev_mmdd
        if prev_rp in rp_to_val:
            cur = s.values[i]
            prev = rp_to_val[prev_rp]
            if pd.notna(cur) and pd.notna(prev) and prev != 0:
                result.iloc[i] = (cur - prev) / abs(prev)
    return result


def _ttm_yoy(s):
    """TTM year-over-year growth rate."""
    return _yoy(s)


def _avg_bs(series):
    """Calculate beginning-ending average for balance sheet items."""
    prev = series.shift(1)
    avg = (prev + series) / 2
    avg = avg.where(prev.notna(), series)
    return avg


def _safe_diff(series, rp_index):
    """Safe difference with quarterly interval check."""
    result = series.copy()
    rp_dt = pd.to_datetime(rp_index)
    for i in range(len(result)):
        if i == 0:
            result.iloc[i] = np.nan
            continue
        delta = (rp_dt[i] - rp_dt[i - 1]).days
        if 75 <= delta <= 110:
            prev_val = series.iloc[i - 1]
            cur_val = series.iloc[i]
            if pd.notna(cur_val) and pd.notna(prev_val):
                result.iloc[i] = cur_val - prev_val
            else:
                result.iloc[i] = np.nan
        else:
            result.iloc[i] = np.nan
    return result


def _filter_statements(df):
    """Filter financial statements: keep consolidated reports only."""
    if df is None or df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    if 'STATEMENT_TYPE' in df.columns:
        st = df['STATEMENT_TYPE'].astype(str)
        mask &= st == '1'
    filtered = df[mask].copy()
    if filtered.empty:
        return df
    if 'ACTUAL_ANN_DATE' in filtered.columns and 'REPORTING_PERIOD' in filtered.columns:
        filtered = filtered.sort_values(['REPORTING_PERIOD', 'ACTUAL_ANN_DATE'])
        filtered = filtered.drop_duplicates('REPORTING_PERIOD', keep='last')
    return filtered


def _prep(bs, inc, cf):
    """Preprocess three statements."""
    bs = _filter_statements(bs).copy()
    inc = _filter_statements(inc).copy()
    cf = _filter_statements(cf).copy()
    bs = bs.sort_values('REPORTING_PERIOD').drop_duplicates('REPORTING_PERIOD', keep='last').reset_index(drop=True)
    inc = inc.sort_values('REPORTING_PERIOD').drop_duplicates('REPORTING_PERIOD', keep='last').reset_index(drop=True)
    cf = cf.sort_values('REPORTING_PERIOD').drop_duplicates('REPORTING_PERIOD', keep='last').reset_index(drop=True)
    return bs, inc, cf


def _safe_col(df, col, default=np.nan):
    """Safely get column, return default if not exists."""
    if col in df.columns:
        return df[col].astype(float)
    return pd.Series(default, index=df.index)


def _pit_fill(fin_df, field, trade_dates):
    """Point-in-time forward fill."""
    if fin_df is None or fin_df.empty or field not in fin_df.columns:
        return pd.Series(np.nan, index=trade_dates)
    df = fin_df.copy()
    if 'ACTUAL_ANN_DATE' in df.columns:
        date_col = 'ACTUAL_ANN_DATE'
    else:
        date_col = 'REPORTING_PERIOD'
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.dropna(subset=[date_col, field])
    df = df.sort_values(date_col)
    s = df.set_index(date_col)[field].astype(float)
    s = s[~s.index.duplicated(keep='last')].sort_index()
    return s.reindex(trade_dates, method='ffill')


def _equity_pit(equity_structure, code, field, trade_dates):
    """Equity structure point-in-time fill."""
    if equity_structure is None or equity_structure.empty:
        return pd.Series(np.nan, index=trade_dates)
    eq = equity_structure[equity_structure['MARKET_CODE'] == code].copy()
    if eq.empty or field not in eq.columns:
        return pd.Series(np.nan, index=trade_dates)
    eq = eq.sort_values('CHANGE_DATE').drop_duplicates('CHANGE_DATE', keep='last')
    s = eq.set_index('CHANGE_DATE')[field].astype(float)
    s.index = pd.to_datetime(s.index)
    s = s[~s.index.duplicated(keep='last')].sort_index()
    return s.reindex(trade_dates, method='ffill')


def _ts_pit(date_index, value_series, trade_dates):
    """Generic point-in-time forward fill."""
    if hasattr(date_index, 'values'):
        di = date_index.values
    elif isinstance(date_index, (list, tuple)):
        di = np.array(date_index)
    else:
        di = np.asarray(date_index)
    if hasattr(value_series, 'values'):
        vs = value_series.values
    elif isinstance(value_series, (list, tuple)):
        vs = np.array(value_series, dtype=float)
    else:
        vs = np.asarray(value_series, dtype=float)
    if len(di) == 0 or len(vs) == 0:
        return pd.Series(np.nan, index=trade_dates)
    src = pd.Series(vs, index=pd.to_datetime(di))
    src = src[~src.index.duplicated(keep='last')].sort_index()
    return src.reindex(trade_dates, method='ffill')


# ============================================================
# 1. Profitability (9 indicators)
# ============================================================

def calc_profitability(bs, inc, cf):
    """Calculate profitability indicators."""
    bs, inc, cf = _prep(bs, inc, cf)
    rp_list = sorted(set(bs['REPORTING_PERIOD']) & set(inc['REPORTING_PERIOD']) & set(cf['REPORTING_PERIOD']))
    if not rp_list:
        return pd.DataFrame()

    bs_a = bs[bs['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()
    inc_a = inc[inc['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()
    cf_a = cf[cf['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()

    np_ttm = get_ttm(inc.reset_index(drop=True), 'NET_PRO_EXCL_MIN_INT_INC')
    inc_tmp = inc.copy()
    inc_tmp['np_ttm'] = np_ttm.values
    cf_ttm = get_ttm(cf.reset_index(drop=True), 'NET_CASH_FLOWS_OPERA_ACT')
    cf_tmp = cf.copy()
    cf_tmp['cf_ttm'] = cf_ttm.values
    ebit_ttm = get_ttm(inc.reset_index(drop=True), 'EBIT')
    inc_tmp['ebit_ttm'] = ebit_ttm.values
    tax_cf_ttm = get_ttm(cf.reset_index(drop=True), 'PAY_ALL_TAX')
    cf_tmp['tax_cf_ttm'] = tax_cf_ttm.values

    inc_s = inc_tmp[inc_tmp['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()
    cf_s = cf_tmp[cf_tmp['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()

    ta = _safe_col(bs_a, 'TOTAL_ASSETS')
    ne = _safe_col(bs_a, 'TOT_SHARE_EQUITY_EXCL_MIN_INT')
    eq_incl = _safe_col(bs_a, 'TOT_SHARE_EQUITY_INCL_MIN_INT')

    ta_avg = _avg_bs(ta)
    ne_avg = _avg_bs(ne)
    eq_incl_avg = _avg_bs(eq_incl)

    st_borrow = _safe_col(bs_a, 'ST_BORROWING').fillna(0)
    lt_loan = _safe_col(bs_a, 'LT_LOAN').fillna(0)
    bonds = _safe_col(bs_a, 'BONDS_PAYABLE').fillna(0)
    noncur_1y = _safe_col(bs_a, 'NONCUR_LIAB_DUE_WITHIN_1Y').fillna(0)
    interest_bearing_debt = st_borrow + lt_loan + bonds + noncur_1y
    invested_capital = eq_incl + interest_bearing_debt
    invested_capital_avg = _avg_bs(invested_capital)

    f = pd.DataFrame(index=bs_a.index)
    f['全部资产现金回收率TTM'] = safe_div(cf_s['cf_ttm'].reindex(f.index).values, ta_avg.values)
    f['全部资产现金回收率变动'] = _safe_diff(f['全部资产现金回收率TTM'], f.index)
    f['资产回报率TTM'] = safe_div(inc_s['np_ttm'].reindex(f.index).values, ta_avg.values)
    f['资产回报率变动'] = _safe_diff(f['资产回报率TTM'], f.index)
    f['净资产收益率TTM'] = safe_div(inc_s['np_ttm'].reindex(f.index).values, ne_avg.values)
    f['净资产收益率变动'] = _safe_diff(f['净资产收益率TTM'], f.index)

    inc_tax_ttm = get_ttm(inc.reset_index(drop=True), 'INCOME_TAX')
    total_profit_ttm = get_ttm(inc.reset_index(drop=True), 'TOTAL_PROFIT')
    inc_tmp2 = inc.copy()
    inc_tmp2['inc_tax_ttm'] = inc_tax_ttm.values
    inc_tmp2['total_profit_ttm'] = total_profit_ttm.values
    inc_s2_tax = inc_tmp2[inc_tmp2['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()
    effective_tax_rate = safe_div(inc_s2_tax['inc_tax_ttm'].reindex(f.index).values,
                                  inc_s2_tax['total_profit_ttm'].reindex(f.index).values)
    effective_tax_rate = np.where(
        np.isnan(effective_tax_rate) | (effective_tax_rate < 0) | (effective_tax_rate > 1),
        0.25, effective_tax_rate)
    ebit_after_tax = inc_s['ebit_ttm'].reindex(f.index).values * (1 - effective_tax_rate)
    f['资本回报率TTM'] = safe_div(ebit_after_tax, invested_capital_avg.values)
    f['资本回报率变动'] = _safe_diff(f['资本回报率TTM'], f.index)

    tax_payable = _safe_col(bs_a, 'TAX_PAYABLE')
    tax_payable_prev = tax_payable.shift(4)
    tax_cf = cf_s['tax_cf_ttm'].reindex(f.index)
    f['税费负担占净资产比'] = safe_div((tax_payable - tax_payable_prev + tax_cf).values, ne_avg.values)

    return f


# ============================================================
# 2. Growth (21 indicators)
# ============================================================

def calc_growth(bs, inc, cf):
    """Calculate growth indicators."""
    bs, inc, cf = _prep(bs, inc, cf)
    rp_list = sorted(set(inc['REPORTING_PERIOD']))
    if not rp_list:
        return pd.DataFrame()

    inc_a = inc.set_index('REPORTING_PERIOD').sort_index()
    cf_a = cf.set_index('REPORTING_PERIOD').sort_index()
    bs_a = bs.set_index('REPORTING_PERIOD').sort_index()

    f = pd.DataFrame(index=inc_a.index)

    np_ttm = get_ttm(inc, 'NET_PRO_EXCL_MIN_INT_INC')
    inc['np_ttm'] = np_ttm.values
    nr_ttm = get_ttm(inc, 'NET_PRO_AFTER_DED_NR_GL')
    if nr_ttm.isna().all():
        inc_for_nr = inc.copy()
        np_val = _safe_col(inc_for_nr, 'NET_PRO_EXCL_MIN_INT_INC')
        non_oper_inc = _safe_col(inc_for_nr, 'NON_OPER_INCOME').fillna(0)
        non_oper_exp = _safe_col(inc_for_nr, 'NON_OPER_EXP').fillna(0)
        inc_for_nr['_NR_EST'] = np_val - non_oper_inc + non_oper_exp
        nr_ttm = get_ttm(inc_for_nr.reset_index(drop=True), '_NR_EST')
    inc['nr_ttm'] = nr_ttm.values
    rev_ttm = get_ttm(inc, 'OPERA_REV')
    inc['rev_ttm'] = rev_ttm.values
    op_ttm = get_ttm(inc, 'OPERA_PROFIT')
    inc['op_ttm'] = op_ttm.values
    cf_ttm_s = get_ttm(cf, 'NET_CASH_FLOWS_OPERA_ACT')
    cf['cf_ttm'] = cf_ttm_s.values

    inc_s = inc.set_index('REPORTING_PERIOD').sort_index()
    cf_s = cf.set_index('REPORTING_PERIOD').sort_index()

    sq_np = get_single_quarter(inc.reset_index(drop=True), 'NET_PRO_EXCL_MIN_INT_INC')
    inc_reset = inc.reset_index(drop=True)
    inc_reset['sq_np'] = sq_np.values
    inc_for_eps = inc.reset_index(drop=True).copy()
    np_sq_for_eps = get_single_quarter(inc_for_eps, 'NET_PRO_EXCL_MIN_INT_INC')
    bs_for_eps = bs.set_index('REPORTING_PERIOD').sort_index()
    tot_s = _safe_col(bs_for_eps, 'TOT_SHARE').reindex(inc_for_eps.set_index('REPORTING_PERIOD').sort_index().index)
    sq_eps = pd.Series(safe_div(np_sq_for_eps.values, tot_s.values), index=np_sq_for_eps.index)
    inc_reset['sq_eps'] = sq_eps.values
    sq_op = get_single_quarter(inc.reset_index(drop=True), 'OPERA_PROFIT')
    inc_reset['sq_op'] = sq_op.values
    sq_rev = get_single_quarter(inc.reset_index(drop=True), 'OPERA_REV')
    inc_reset['sq_rev'] = sq_rev.values
    sq_cf = get_single_quarter(cf.reset_index(drop=True), 'NET_CASH_FLOWS_OPERA_ACT')
    cf_reset = cf.reset_index(drop=True)
    cf_reset['sq_cf'] = sq_cf.values

    inc_sq = inc_reset.set_index('REPORTING_PERIOD').sort_index()
    cf_sq = cf_reset.set_index('REPORTING_PERIOD').sort_index()

    rev = _safe_col(inc_a, 'OPERA_REV')
    f['营业收入增速'] = _yoy(rev)

    if 'BASIC_EPS' in inc_a.columns and inc_a['BASIC_EPS'].notna().any():
        f['每股盈利'] = _safe_col(inc_a, 'BASIC_EPS').reindex(f.index)
    else:
        np_val = _safe_col(inc_a, 'NET_PRO_EXCL_MIN_INT_INC').reindex(f.index)
        tot_s = _safe_col(bs_a, 'TOT_SHARE').reindex(f.index)
        f['每股盈利'] = pd.Series(safe_div(np_val.values, tot_s.values), index=f.index)

    f['每股盈利增速_单季度同比'] = _yoy(inc_sq['sq_eps'].reindex(f.index))
    np_ttm_for_eps = inc_s['np_ttm'].reindex(f.index)
    bs_tot_share = _safe_col(bs_a, 'TOT_SHARE').reindex(f.index)
    eps_ttm_series = pd.Series(safe_div(np_ttm_for_eps.values, bs_tot_share.values), index=f.index)
    f['每股盈利增速_TTM同比'] = _ttm_yoy(eps_ttm_series)
    f['扣非净利润增速_TTM同比'] = _ttm_yoy(inc_s['nr_ttm'].reindex(f.index))
    f['净利润增速_单季度同比'] = _yoy(inc_sq['sq_np'].reindex(f.index))
    f['净利润增速_单季度环比'] = _qoq(inc_sq['sq_np'].reindex(f.index))
    f['净利润增速_TTM同比'] = _ttm_yoy(inc_s['np_ttm'].reindex(f.index))
    f['经营现金流增速_单季度环比'] = _qoq(cf_sq['sq_cf'].reindex(f.index))
    f['经营现金流增速_单季度同比'] = _yoy(cf_sq['sq_cf'].reindex(f.index))
    f['经营现金流增速_TTM同比'] = _ttm_yoy(cf_s['cf_ttm'].reindex(f.index))
    f['营业利润增速_单季度同比'] = _yoy(inc_sq['sq_op'].reindex(f.index))
    f['营业利润增速_单季度环比'] = _qoq(inc_sq['sq_op'].reindex(f.index))
    f['营业利润增速_TTM同比'] = _ttm_yoy(inc_s['op_ttm'].reindex(f.index))
    f['营业收入增速_单季度同比'] = _yoy(inc_sq['sq_rev'].reindex(f.index))
    f['营业收入增速_单季度环比'] = _qoq(inc_sq['sq_rev'].reindex(f.index))
    f['营业收入增速_TTM同比'] = _ttm_yoy(inc_s['rev_ttm'].reindex(f.index))

    sq_ne = get_single_quarter(inc.reset_index(drop=True), 'NET_PRO_EXCL_MIN_INT_INC')
    bs_rp = bs.set_index('REPORTING_PERIOD').sort_index()
    ne_aligned = _safe_col(bs_rp, 'TOT_SHARE_EQUITY_EXCL_MIN_INT').reindex(f.index)
    ne_avg_aligned = _avg_bs(ne_aligned)
    sq_roe = pd.Series(safe_div(
        pd.Series(sq_ne.values, index=inc.set_index('REPORTING_PERIOD').sort_index().index).reindex(f.index).values,
        ne_avg_aligned.values
    ), index=f.index)
    f['净资产收益率增速_单季度同比'] = _yoy(sq_roe)
    f['净资产收益率增速_单季度环比'] = _qoq(sq_roe)

    roe_ttm = pd.Series(safe_div(
        inc_s['np_ttm'].reindex(f.index).values,
        ne_avg_aligned.values
    ), index=f.index)
    f['净资产收益率增速_TTM同比'] = _ttm_yoy(roe_ttm)

    ta = _safe_col(bs_rp, 'TOTAL_ASSETS').reindex(f.index)
    f['总资产增速'] = _yoy(ta)

    return f


# ============================================================
# 3. Efficiency (15 indicators)
# ============================================================

def calc_efficiency(bs, inc, cf):
    """Calculate efficiency indicators."""
    bs, inc, cf = _prep(bs, inc, cf)
    rp_list = sorted(set(bs['REPORTING_PERIOD']) & set(inc['REPORTING_PERIOD']))
    if not rp_list:
        return pd.DataFrame()

    bs_a = bs[bs['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()
    inc_a = inc[inc['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()

    rev_ttm = get_ttm(inc, 'OPERA_REV')
    inc['rev_ttm'] = rev_ttm.values
    cost_ttm = get_ttm(inc, 'LESS_OPERA_COST')
    inc['cost_ttm'] = cost_ttm.values
    np_ttm = get_ttm(inc, 'NET_PRO_EXCL_MIN_INT_INC')
    inc['np_ttm'] = np_ttm.values
    op_ttm = get_ttm(inc, 'OPERA_PROFIT')
    inc['op_ttm'] = op_ttm.values
    fin_ttm = get_ttm(inc, 'LESS_FIN_EXP')
    inc['fin_ttm'] = fin_ttm.values
    sell_ttm = get_ttm(inc, 'LESS_SELLING_EXP')
    inc['sell_ttm'] = sell_ttm.values

    inc_s = inc[inc['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()

    f = pd.DataFrame(index=bs_a.index)
    ta = _safe_col(bs_a, 'TOTAL_ASSETS')
    ta_avg = _avg_bs(ta)

    f['资产周转率TTM'] = safe_div(inc_s['rev_ttm'].reindex(f.index).values, ta_avg.values)
    f['资产周转率变动'] = _safe_diff(f['资产周转率TTM'], f.index)

    gross_margin_ttm = safe_div(
        (inc_s['rev_ttm'].reindex(f.index) - inc_s['cost_ttm'].reindex(f.index)).values,
        inc_s['rev_ttm'].reindex(f.index).values
    )
    f['毛利率变动'] = _safe_diff(pd.Series(gross_margin_ttm, index=f.index), f.index)

    inv = _safe_col(bs_a, 'INV')
    inv_avg = (inv + inv.shift(1) + inv.shift(2) + inv.shift(3) + inv.shift(4)) / 5
    inv_avg_2pt = (inv + inv.shift(1)) / 2
    inv_avg = inv_avg.where(inv_avg.notna(), inv_avg_2pt)
    inv_avg = inv_avg.where(inv_avg.notna(), inv)
    f['存货周转率TTM'] = safe_div(inc_s['cost_ttm'].reindex(f.index).values, inv_avg.values)
    f['存货周转率变动'] = _safe_diff(f['存货周转率TTM'], f.index)

    f['净利率TTM'] = safe_div(inc_s['np_ttm'].reindex(f.index).values, inc_s['rev_ttm'].reindex(f.index).values)
    f['营业利润率TTM'] = safe_div(inc_s['op_ttm'].reindex(f.index).values, inc_s['rev_ttm'].reindex(f.index).values)
    f['营业利润率变动'] = _safe_diff(f['营业利润率TTM'], f.index)

    gross_profit_ttm = inc_s['rev_ttm'].reindex(f.index) - inc_s['cost_ttm'].reindex(f.index)
    f['营业利润比毛利润'] = safe_div(inc_s['op_ttm'].reindex(f.index).values, gross_profit_ttm.values)

    ar = _safe_col(bs_a, 'ACCT_RECEIVABLE').fillna(0)
    nr = _safe_col(bs_a, 'NOTES_RECEIVABLE').fillna(0)
    recv = ar + nr
    recv_avg = (recv + recv.shift(1) + recv.shift(2) + recv.shift(3) + recv.shift(4)) / 5
    recv_avg_2pt = (recv + recv.shift(1)) / 2
    recv_avg = recv_avg.where(recv_avg.notna(), recv_avg_2pt)
    recv_avg = recv_avg.where(recv_avg.notna(), recv)
    f['应收周转率TTM'] = safe_div(inc_s['rev_ttm'].reindex(f.index).values, recv_avg.values)
    f['应收周转率变动'] = _safe_diff(f['应收周转率TTM'], f.index)

    f['财务费用率TTM'] = safe_div(inc_s['fin_ttm'].reindex(f.index).values, inc_s['rev_ttm'].reindex(f.index).values)
    f['财务费用率变动'] = _safe_diff(f['财务费用率TTM'], f.index)
    f['销售费用率TTM'] = safe_div(inc_s['sell_ttm'].reindex(f.index).values, inc_s['rev_ttm'].reindex(f.index).values)
    f['销售费用率变动'] = _safe_diff(f['销售费用率TTM'], f.index)

    return f


# ============================================================
# 4. Earnings Quality (8 indicators)
# ============================================================

def calc_earnings_quality(bs, inc, cf):
    """Calculate earnings quality indicators."""
    bs, inc, cf = _prep(bs, inc, cf)
    rp_list = sorted(set(bs['REPORTING_PERIOD']) & set(inc['REPORTING_PERIOD']) & set(cf['REPORTING_PERIOD']))
    if not rp_list:
        return pd.DataFrame()

    bs_a = bs[bs['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()
    inc_a = inc[inc['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()

    op_ttm = get_ttm(inc, 'OPERA_PROFIT')
    inc['op_ttm'] = op_ttm.values
    rev_ttm = get_ttm(inc, 'OPERA_REV')
    inc['rev_ttm'] = rev_ttm.values
    cf_ttm_s = get_ttm(cf, 'NET_CASH_FLOWS_OPERA_ACT')
    cf['cf_ttm'] = cf_ttm_s.values

    inc_s = inc[inc['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()
    cf_s = cf[cf['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()

    f = pd.DataFrame(index=bs_a.index)

    op_ttm_a = inc_s['op_ttm'].reindex(f.index)
    cf_ttm_a = cf_s['cf_ttm'].reindex(f.index)
    rev_ttm_a = inc_s['rev_ttm'].reindex(f.index)

    accrual = op_ttm_a - cf_ttm_a
    f['应计利润占比'] = safe_div(accrual.values, op_ttm_a.values)
    f['应计利润占比变动'] = _safe_diff(f['应计利润占比'], f.index)

    cash = _safe_col(bs_a, 'CURRENCY_CAP')
    if cf is not None and not cf.empty:
        cf_rp = cf[cf['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()
        if 'END_BAL_CASH_CASH_EQU' in cf_rp.columns:
            cash = _safe_col(cf_rp, 'END_BAL_CASH_CASH_EQU').reindex(f.index)
    cur_liab = _safe_col(bs_a, 'TOTAL_CUR_LIAB')
    f['现金比率'] = safe_div(cash.values, cur_liab.values)
    f['现金比率变动'] = _safe_diff(f['现金比率'], f.index)

    f['经营现金流比营业收入'] = safe_div(cf_ttm_a.values, rev_ttm_a.values)
    f['经营现金流比营业收入变动'] = _safe_diff(f['经营现金流比营业收入'], f.index)
    f['经营现金流比营业利润'] = safe_div(cf_ttm_a.values, op_ttm_a.values)
    f['经营现金流比营业利润变动'] = _safe_diff(f['经营现金流比营业利润'], f.index)

    return f


# ============================================================
# 5. Safety (14 indicators)
# ============================================================

def calc_safety(bs, inc, cf):
    """Calculate safety indicators."""
    bs, inc, cf = _prep(bs, inc, cf)
    rp_list = sorted(set(bs['REPORTING_PERIOD']) & set(cf['REPORTING_PERIOD']))
    if not rp_list:
        return pd.DataFrame()

    bs_a = bs[bs['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()
    cf_ttm_s = get_ttm(cf, 'NET_CASH_FLOWS_OPERA_ACT')
    cf['cf_ttm'] = cf_ttm_s.values
    cf_s = cf[cf['REPORTING_PERIOD'].isin(rp_list)].set_index('REPORTING_PERIOD').sort_index()

    f = pd.DataFrame(index=bs_a.index)

    ta = _safe_col(bs_a, 'TOTAL_ASSETS')
    tl = _safe_col(bs_a, 'TOTAL_LIAB')
    cur_liab = _safe_col(bs_a, 'TOTAL_CUR_LIAB')
    cur_asset = _safe_col(bs_a, 'TOTAL_CUR_ASSETS')
    noncur_liab = _safe_col(bs_a, 'TOTAL_NONCUR_LIAB')
    ne = _safe_col(bs_a, 'TOT_SHARE_EQUITY_EXCL_MIN_INT')
    inv = _safe_col(bs_a, 'INV').fillna(0)
    prepay = _safe_col(bs_a, 'PREPAYMENT').fillna(0)
    cf_ttm_a = cf_s['cf_ttm'].reindex(f.index)

    f['流动负债占比'] = safe_div(cur_liab.values, tl.values)
    f['流动负债占比变动'] = _safe_diff(f['流动负债占比'], f.index)
    f['长期负债占比'] = safe_div(noncur_liab.values, tl.values)
    f['长期负债占比变动'] = _safe_diff(f['长期负债占比'], f.index)
    f['现金流动负债比率'] = safe_div(cf_ttm_a.values, cur_liab.values)
    f['现金流动负债比率变动'] = _safe_diff(f['现金流动负债比率'], f.index)
    f['流动比率'] = safe_div(cur_asset.values, cur_liab.values)
    f['流动比率变动'] = _safe_diff(f['流动比率'], f.index)

    alr = pd.Series(safe_div(tl.values, ta.values), index=f.index)
    f['资产负债率变动'] = _safe_diff(alr, f.index)
    f['资产负债比'] = alr
    f['产权比率'] = safe_div(tl.values, ne.values)
    f['产权比率变动'] = _safe_diff(f['产权比率'], f.index)

    unamortized = _safe_col(bs_a, 'UNAMORTIZED_EXP').fillna(0)
    quick_asset = cur_asset - inv - prepay - unamortized
    f['速动比率'] = safe_div(quick_asset.values, cur_liab.values)
    f['速动比率变动'] = _safe_diff(f['速动比率'], f.index)

    return f


# ============================================================
# 6. Governance (2 indicators) - Daily
# ============================================================

def calc_governance(code, inc, equity_structure, dividend, trade_dates):
    """Calculate governance indicators (daily frequency)."""
    f = pd.DataFrame(index=trade_dates)

    float_a = _equity_pit(equity_structure, code, 'FLOAT_A_SHARE', trade_dates)
    tot_s = _equity_pit(equity_structure, code, 'TOT_SHARE', trade_dates)
    f['流通股占比'] = safe_div(float_a.values, tot_s.values)

    f['股利支付率'] = np.nan
    if dividend is not None and not dividend.empty and inc is not None and not inc.empty:
        div = dividend[dividend['MARKET_CODE'] == code].copy()
        inc_f = _filter_statements(inc)
        if not div.empty and not inc_f.empty and 'DVD_PER_SHARE_PRE_TAX_CASH' in div.columns and 'REPORT_PERIOD' in div.columns:
            div = div.sort_values('REPORT_PERIOD').drop_duplicates('REPORT_PERIOD', keep='last')
            inc_sorted = inc_f.sort_values('REPORTING_PERIOD').drop_duplicates('REPORTING_PERIOD', keep='last')
            inc_sorted = inc_sorted.set_index('REPORTING_PERIOD')
            payout_dates = []
            payout_vals = []
            for _, row in div.iterrows():
                rp = row['REPORT_PERIOD']
                dps = float(row['DVD_PER_SHARE_PRE_TAX_CASH']) if pd.notna(row['DVD_PER_SHARE_PRE_TAX_CASH']) else 0
                base_share = float(row['DIV_BASESHARE']) if 'DIV_BASESHARE' in div.columns and pd.notna(row.get('DIV_BASESHARE')) else np.nan
                total_div = dps * base_share if pd.notna(base_share) else np.nan
                if rp in inc_sorted.index and 'NET_PRO_EXCL_MIN_INT_INC' in inc_sorted.columns:
                    np_val = float(inc_sorted.loc[rp, 'NET_PRO_EXCL_MIN_INT_INC'])
                    if pd.notna(total_div) and pd.notna(np_val) and np_val != 0:
                        payout_dates.append(rp)
                        payout_vals.append(total_div / np_val)
            if payout_dates:
                f['股利支付率'] = _ts_pit(pd.Index(payout_dates), pd.Series(payout_vals), trade_dates)

    return f


# ============================================================
# 7. Valuation (12 indicators) - Daily
# ============================================================

def _calc_dividend_yield(code, dividend, equity_structure, close, trade_dates, tot_share, mc):
    """Calculate dividend yield (point-in-time)."""
    result = pd.Series(np.nan, index=trade_dates)
    if dividend is None or dividend.empty:
        return result.values
    div_code = dividend[dividend['MARKET_CODE'] == code].copy()
    if div_code.empty or 'DVD_PER_SHARE_PRE_TAX_CASH' not in div_code.columns:
        return result.values

    date_col = 'DATE_DVD_PAYOUT'
    if date_col not in div_code.columns or div_code[date_col].isna().all():
        date_col = 'REPORT_PERIOD'
    if date_col not in div_code.columns:
        return result.values

    div_code = div_code[div_code[date_col].notna() & (div_code[date_col].astype(str) != '')]
    if div_code.empty:
        return result.values

    div_code[date_col] = pd.to_datetime(div_code[date_col])
    div_code = div_code.sort_values(date_col)
    dps_vals = div_code['DVD_PER_SHARE_PRE_TAX_CASH'].astype(float).fillna(0)
    if 'DIV_BASESHARE' in div_code.columns:
        base_shares = div_code['DIV_BASESHARE'].astype(float)
    else:
        base_shares = pd.Series(np.nan, index=div_code.index)

    div_amounts = []
    div_dates_list = []
    for idx in div_code.index:
        dps = dps_vals.loc[idx]
        bs_val = base_shares.loc[idx]
        d = div_code.loc[idx, date_col]
        amount = dps * bs_val * 10000 if pd.notna(bs_val) else np.nan
        div_amounts.append(amount)
        div_dates_list.append(d)

    if not div_dates_list:
        return result.values

    div_pit = _ts_pit(pd.Index(div_dates_list), pd.Series(div_amounts), trade_dates)
    dps_pit = _ts_pit(pd.Index(div_dates_list), dps_vals.reset_index(drop=True), trade_dates)
    div_pit_filled = div_pit.copy()
    na_mask = div_pit.isna() & dps_pit.notna()
    div_pit_filled[na_mask] = dps_pit[na_mask] * tot_share[na_mask] * 10000

    return safe_div(div_pit_filled.values, mc)


def _calc_dividend_yield_ttm(code, dividend, equity_structure, trade_dates, tot_share, mc):
    """Calculate TTM dividend yield."""
    result = pd.Series(np.nan, index=trade_dates)
    if dividend is None or dividend.empty:
        return result.values
    div_code = dividend[dividend['MARKET_CODE'] == code].copy()
    if div_code.empty or 'DVD_PER_SHARE_PRE_TAX_CASH' not in div_code.columns:
        return result.values

    date_col = 'DATE_DVD_PAYOUT'
    if date_col not in div_code.columns or div_code[date_col].isna().all():
        date_col = 'REPORT_PERIOD'
    if date_col not in div_code.columns:
        return result.values

    div_code = div_code[div_code[date_col].notna() & (div_code[date_col].astype(str) != '')]
    if div_code.empty:
        return result.values

    div_code[date_col] = pd.to_datetime(div_code[date_col])
    div_code = div_code.sort_values(date_col)
    dps_vals = div_code['DVD_PER_SHARE_PRE_TAX_CASH'].astype(float).fillna(0)

    if 'DIV_BASESHARE' in div_code.columns:
        base_shares = div_code['DIV_BASESHARE'].astype(float)
    else:
        base_shares = pd.Series(np.nan, index=div_code.index)

    div_dates = div_code[date_col].values
    td_dt = trade_dates.to_numpy()

    div_total_daily = np.full(len(trade_dates), np.nan)
    for j in range(len(trade_dates)):
        td = td_dt[j]
        lookback = td - np.timedelta64(365, 'D')
        mask = (div_dates > lookback) & (div_dates <= td)
        if mask.any():
            total = 0.0
            for idx in div_code.index[mask]:
                dps = dps_vals.loc[idx]
                bs_val = base_shares.loc[idx]
                if pd.isna(bs_val):
                    bs_val = tot_share.iloc[j] if pd.notna(tot_share.iloc[j]) else 0
                total += dps * bs_val * 10000
            if total > 0:
                div_total_daily[j] = total

    return safe_div(div_total_daily, mc)


def calc_valuation(code, bs, inc, cf, dividend, kline, equity_structure):
    """Calculate valuation indicators (daily frequency)."""
    if kline is None or kline.empty:
        return pd.DataFrame()

    kl = kline.copy()
    kl['kline_time'] = pd.to_datetime(kl['kline_time'])
    kl = kl.sort_values('kline_time').drop_duplicates('kline_time', keep='last')
    trade_dates = kl.set_index('kline_time').index
    close = kl.set_index('kline_time')['close'].astype(float)

    tot_share = _equity_pit(equity_structure, code, 'TOT_SHARE', trade_dates)
    total_mkt_cap = close * tot_share * 10000

    bs_f = _filter_statements(bs) if bs is not None and not bs.empty else pd.DataFrame()
    inc_f = _filter_statements(inc) if inc is not None and not inc.empty else pd.DataFrame()
    cf_f = _filter_statements(cf) if cf is not None and not cf.empty else pd.DataFrame()
    for df in [bs_f, inc_f, cf_f]:
        if not df.empty:
            df.sort_values('REPORTING_PERIOD', inplace=True)
            df.drop_duplicates('REPORTING_PERIOD', keep='last', inplace=True)

    ne = _pit_fill(bs_f, 'TOT_SHARE_EQUITY_EXCL_MIN_INT', trade_dates)
    np_last = _pit_fill(inc_f, 'NET_PRO_EXCL_MIN_INT_INC', trade_dates)
    rev_last = _pit_fill(inc_f, 'OPERA_REV', trade_dates)
    cf_op_last = _pit_fill(cf_f, 'NET_CASH_FLOWS_OPERA_ACT', trade_dates)

    def _ttm_pit(df, field):
        if df is None or df.empty:
            return pd.Series(np.nan, index=trade_dates)
        ttm_s = get_ttm(df.reset_index(drop=True), field)
        tmp = df.copy()
        tmp['_ttm'] = ttm_s.values
        return _pit_fill(tmp, '_ttm', trade_dates)

    np_ttm = _ttm_pit(inc_f, 'NET_PRO_EXCL_MIN_INT_INC')
    rev_ttm = _ttm_pit(inc_f, 'OPERA_REV')
    cf_ttm = _ttm_pit(cf_f, 'NET_CASH_FLOWS_OPERA_ACT')
    fcf_ttm = _ttm_pit(cf_f, 'FREE_CASH_FLOW')
    ncf_ttm = _ttm_pit(cf_f, 'NET_INCR_CASH_AND_CASH_EQU')

    mc = total_mkt_cap.values
    f = pd.DataFrame(index=trade_dates)

    f['市净率'] = safe_div(mc, ne.values)
    f['市现率'] = safe_div(mc, cf_op_last.values)
    f['市盈率'] = safe_div(mc, np_last.values)
    f['股息率'] = _calc_dividend_yield(code, dividend, equity_structure, close, trade_dates, tot_share, mc)
    f['市销率'] = safe_div(mc, rev_last.values)
    f['市现率TTM'] = safe_div(mc, cf_ttm.values)
    f['市盈率TTM'] = safe_div(mc, np_ttm.values)
    f['股息率TTM'] = _calc_dividend_yield_ttm(code, dividend, equity_structure, trade_dates, tot_share, mc)
    f['市销率TTM'] = safe_div(mc, rev_ttm.values)
    f['自由现金流TTM比总市值'] = safe_div(fcf_ttm.values, mc)
    f['净现金流TTM比总市值'] = safe_div(ncf_ttm.values, mc)

    np_ttm_series = np_ttm.copy()
    np_ttm_series.index = pd.to_datetime(np_ttm_series.index)
    prev_dates = np_ttm_series.index - pd.DateOffset(years=1)
    np_ttm_prev_aligned = np_ttm_series.reindex(prev_dates, method='ffill')
    np_ttm_prev_aligned.index = np_ttm_series.index
    growth = safe_div((np_ttm_series - np_ttm_prev_aligned).values, np.abs(np_ttm_prev_aligned.values))
    pe_ttm_vals = f['市盈率TTM'].values
    f['市盈率相对盈利增长率'] = safe_div(pe_ttm_vals, growth * 100)

    return f


# ============================================================
# 8. Shareholder (4 indicators) - Daily
# ============================================================

def calc_shareholder(code, holder_num, share_holder, trade_dates):
    """Calculate shareholder indicators (daily frequency)."""
    f = pd.DataFrame(index=trade_dates)

    f['股东数目时序标准分数'] = np.nan
    if holder_num is not None and not holder_num.empty:
        hn = holder_num[holder_num['MARKET_CODE'] == code].copy()
        if not hn.empty and 'HOLDER_NUM' in hn.columns:
            hn = hn.sort_values('HOLDER_ENDDATE').drop_duplicates('HOLDER_ENDDATE', keep='last')
            hn_series = hn.set_index('HOLDER_ENDDATE')['HOLDER_NUM'].astype(float).sort_index()
            exp_mean = hn_series.expanding(min_periods=2).mean()
            exp_std = hn_series.expanding(min_periods=2).std().replace(0, np.nan)
            z_score = (hn_series - exp_mean) / exp_std
            f['股东数目时序标准分数'] = _ts_pit(z_score.index.to_series(), z_score, trade_dates)

    f['持仓机构个数'] = np.nan
    f['持仓机构个数变化'] = np.nan
    f['十大股东占比分散度'] = np.nan
    if share_holder is not None and not share_holder.empty:
        sh = share_holder[share_holder['MARKET_CODE'] == code].copy()
        if not sh.empty and 'HOLDER_ENDDATE' in sh.columns:
            sh = sh.sort_values('HOLDER_ENDDATE')
            if 'HOLDER_HOLDER_CATEGORY' in sh.columns:
                holder_category = pd.to_numeric(sh['HOLDER_HOLDER_CATEGORY'], errors='coerce')
                sh_inst = sh[holder_category.eq(2)].copy()
            else:
                sh_inst = sh.iloc[0:0].copy()

            if not sh_inst.empty:
                inst_count = sh_inst.groupby('HOLDER_ENDDATE').size()
                inst_daily = _ts_pit(inst_count.index.to_series(), inst_count, trade_dates)
                f['持仓机构个数'] = inst_daily

                if len(inst_count) >= 2:
                    inst_change_raw = inst_count.diff()
                    inst_change_daily = _ts_pit(inst_change_raw.index.to_series(), inst_change_raw, trade_dates)
                else:
                    inst_change_daily = pd.Series(np.nan, index=trade_dates)
                f['持仓机构个数变化'] = inst_change_daily

            if 'HOLDER_PCT' in sh.columns:
                disp = sh.groupby('HOLDER_ENDDATE')['HOLDER_PCT'].apply(
                    lambda x: x.astype(float).dropna().std() if len(x.dropna()) > 1 else np.nan
                )
                disp_daily = _ts_pit(disp.index.to_series(), disp, trade_dates)
                f['十大股东占比分散度'] = disp_daily

    return f


# ============================================================
# 9. Size (5 indicators) - Daily
# ============================================================

def calc_size(code, kline, equity_structure):
    """Calculate size indicators (daily frequency)."""
    if kline is None or kline.empty:
        return pd.DataFrame()

    kl = kline.copy()
    kl['kline_time'] = pd.to_datetime(kl['kline_time'])
    kl = kl.sort_values('kline_time').drop_duplicates('kline_time', keep='last')
    trade_dates = kl.set_index('kline_time').index
    close = kl.set_index('kline_time')['close'].astype(float)

    tot_share = _equity_pit(equity_structure, code, 'TOT_SHARE', trade_dates)
    float_share = _equity_pit(equity_structure, code, 'FLOAT_A_SHARE', trade_dates)

    total_mkt_cap = close * tot_share * 10000
    float_mkt_cap = close * float_share * 10000

    f = pd.DataFrame(index=trade_dates)
    f['流通市值'] = float_mkt_cap
    f['流通市值比总市值'] = safe_div(float_mkt_cap.values, total_mkt_cap.values)
    f['流通市值对数'] = np.where(float_mkt_cap > 0, np.log(float_mkt_cap), np.nan)
    f['总市值对数'] = np.where(total_mkt_cap > 0, np.log(total_mkt_cap), np.nan)
    f['总市值'] = total_mkt_cap

    return f


# ============================================================
# Main calculation function
# ============================================================

def calc_all_factors_for_stock(code, bs, inc, cf, kline,
                               equity_structure, dividend,
                               holder_num, share_holder):
    """Calculate all indicators for a single stock.
    
    Returns (quarterly_df, daily_df).
    quarterly_df: profitability + growth + efficiency + earnings_quality + safety
    daily_df: valuation + size + governance + shareholder
    """
    quarterly_parts = []

    try:
        prof = calc_profitability(bs, inc, cf)
        if not prof.empty:
            quarterly_parts.append(prof)
    except Exception as e:
        print(f'  [WARN] {code} profitability calc error: {e}')

    try:
        grow = calc_growth(bs, inc, cf)
        if not grow.empty:
            quarterly_parts.append(grow)
    except Exception as e:
        print(f'  [WARN] {code} growth calc error: {e}')

    try:
        eff = calc_efficiency(bs, inc, cf)
        if not eff.empty:
            quarterly_parts.append(eff)
    except Exception as e:
        print(f'  [WARN] {code} efficiency calc error: {e}')

    try:
        eq = calc_earnings_quality(bs, inc, cf)
        if not eq.empty:
            quarterly_parts.append(eq)
    except Exception as e:
        print(f'  [WARN] {code} earnings quality calc error: {e}')

    try:
        saf = calc_safety(bs, inc, cf)
        if not saf.empty:
            quarterly_parts.append(saf)
    except Exception as e:
        print(f'  [WARN] {code} safety calc error: {e}')

    if quarterly_parts:
        q_df = quarterly_parts[0]
        for part in quarterly_parts[1:]:
            for col in part.columns:
                q_df[col] = part[col].reindex(q_df.index)
    else:
        q_df = pd.DataFrame()

    if not q_df.empty:
        q_df.insert(0, 'code', code)

    daily_parts = []

    try:
        val = calc_valuation(code, bs, inc, cf, dividend, kline, equity_structure)
        if not val.empty:
            daily_parts.append(val)
    except Exception as e:
        print(f'  [WARN] {code} valuation calc error: {e}')

    try:
        sz = calc_size(code, kline, equity_structure)
        if not sz.empty:
            daily_parts.append(sz)
    except Exception as e:
        print(f'  [WARN] {code} size calc error: {e}')

    if kline is not None and not kline.empty:
        kl_tmp = kline.copy()
        kl_tmp['kline_time'] = pd.to_datetime(kl_tmp['kline_time'])
        kl_tmp = kl_tmp.sort_values('kline_time').drop_duplicates('kline_time', keep='last')
        trade_dates = kl_tmp.set_index('kline_time').index

        try:
            gov = calc_governance(code, inc, equity_structure, dividend, trade_dates)
            if not gov.empty:
                daily_parts.append(gov)
        except Exception as e:
            print(f'  [WARN] {code} governance calc error: {e}')

        try:
            sh = calc_shareholder(code, holder_num, share_holder, trade_dates)
            if not sh.empty:
                daily_parts.append(sh)
        except Exception as e:
            print(f'  [WARN] {code} shareholder calc error: {e}')

    if daily_parts:
        d_df = daily_parts[0]
        for part in daily_parts[1:]:
            for col in part.columns:
                d_df[col] = part[col].reindex(d_df.index)
    else:
        d_df = pd.DataFrame()

    if not d_df.empty:
        d_df.insert(0, 'code', code)

    return q_df, d_df


# ============================================================
# Category Map
# ============================================================

CATEGORY_MAP = {
    'profitability': {
        'name': '盈利能力',
        'freq': 'quarterly',
        'count': 9,
        'factors': [
            '全部资产现金回收率TTM', '全部资产现金回收率变动',
            '资产回报率TTM', '资产回报率变动',
            '净资产收益率TTM', '净资产收益率变动',
            '资本回报率TTM', '资本回报率变动',
            '税费负担占净资产比'
        ]
    },
    'growth': {
        'name': '成长指标',
        'freq': 'quarterly',
        'count': 21,
        'factors': [
            '营业收入增速', '每股盈利',
            '每股盈利增速_单季度同比', '每股盈利增速_TTM同比',
            '扣非净利润增速_TTM同比',
            '净利润增速_单季度同比', '净利润增速_单季度环比', '净利润增速_TTM同比',
            '经营现金流增速_单季度环比', '经营现金流增速_单季度同比', '经营现金流增速_TTM同比',
            '营业利润增速_单季度同比', '营业利润增速_单季度环比', '营业利润增速_TTM同比',
            '营业收入增速_单季度同比', '营业收入增速_单季度环比', '营业收入增速_TTM同比',
            '净资产收益率增速_单季度同比', '净资产收益率增速_单季度环比', '净资产收益率增速_TTM同比',
            '总资产增速'
        ]
    },
    'efficiency': {
        'name': '营运效率',
        'freq': 'quarterly',
        'count': 15,
        'factors': [
            '资产周转率TTM', '资产周转率变动',
            '毛利率变动',
            '存货周转率TTM', '存货周转率变动',
            '净利率TTM',
            '营业利润率TTM', '营业利润率变动',
            '营业利润比毛利润',
            '应收周转率TTM', '应收周转率变动',
            '财务费用率TTM', '财务费用率变动',
            '销售费用率TTM', '销售费用率变动'
        ]
    },
    'earnings_quality': {
        'name': '盈余质量',
        'freq': 'quarterly',
        'count': 8,
        'factors': [
            '应计利润占比', '应计利润占比变动',
            '现金比率', '现金比率变动',
            '经营现金流比营业收入', '经营现金流比营业收入变动',
            '经营现金流比营业利润', '经营现金流比营业利润变动'
        ]
    },
    'safety': {
        'name': '安全性',
        'freq': 'quarterly',
        'count': 14,
        'factors': [
            '流动负债占比', '流动负债占比变动',
            '长期负债占比', '长期负债占比变动',
            '现金流动负债比率', '现金流动负债比率变动',
            '流动比率', '流动比率变动',
            '资产负债率变动', '资产负债比',
            '产权比率', '产权比率变动',
            '速动比率', '速动比率变动'
        ]
    },
    'governance': {
        'name': '公司治理',
        'freq': 'daily',
        'count': 2,
        'factors': ['流通股占比', '股利支付率']
    },
    'valuation': {
        'name': '估值指标',
        'freq': 'daily',
        'count': 12,
        'factors': [
            '市净率', '市现率', '市盈率', '股息率', '市销率',
            '市现率TTM', '市盈率TTM', '股息率TTM', '市销率TTM',
            '自由现金流TTM比总市值', '净现金流TTM比总市值',
            '市盈率相对盈利增长率'
        ]
    },
    'shareholder': {
        'name': '股东指标',
        'freq': 'daily',
        'count': 4,
        'factors': ['股东数目时序标准分数', '持仓机构个数', '持仓机构个数变化', '十大股东占比分散度']
    },
    'size': {
        'name': '规模指标',
        'freq': 'daily',
        'count': 5,
        'factors': ['流通市值', '流通市值比总市值', '流通市值对数', '总市值对数', '总市值']
    }
}

QUARTERLY_CATEGORIES = ['profitability', 'growth', 'efficiency', 'earnings_quality', 'safety']
DAILY_CATEGORIES = ['governance', 'valuation', 'shareholder', 'size']


def _find_factor_category(factor_name):
    """Find which category a factor belongs to."""
    for cat_key, cat_info in CATEGORY_MAP.items():
        if factor_name in cat_info['factors']:
            return cat_key
    return None


def _format_value(v):
    """Format a single value for JSON output."""
    if isinstance(v, (np.floating, float)):
        if np.isnan(v) or np.isinf(v):
            return None
        return round(float(v), 6)
    if isinstance(v, (np.integer, int)):
        return int(v)
    return v


def _df_to_records(df, max_rows=20):
    """Convert DataFrame to JSON-friendly records."""
    if df is None or df.empty:
        return []
    df_out = df.tail(max_rows).copy()
    records = []
    for idx, row in df_out.iterrows():
        rec = {'period': str(idx)}
        for col in row.index:
            rec[col] = _format_value(row[col])
        records.append(rec)
    return records
