"""Pro-style stock data API routers.

Provides endpoints matching the Pro data platform stock API conventions:
/stock_basic, /trade_cal, /daily, /weekly, /monthly,
/adj_factor, /pro_bar, /suspend_d
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from adshare import dependencies as deps
from adshare.core.logging import get_logger
from adshare.historical.models import normalize_period
from adshare.historical.warehouse import HistoricalWarehouse
from adshare.services.dataframe_formatter import build_response, build_error_response, to_fields_items
from adshare.services.derived_metrics import (
    apply_adjustment,
    build_limit_list,
    compute_moving_averages,
    compute_price_changes,
    convert_volume_to_lots,
    derive_suspensions,
    filter_fields,
    filter_new_shares,
    map_adj_factor_fields,
    map_kline_fields,
    map_stock_basic_fields,
    map_suspend_fields,
    map_trade_cal_fields,
)
from adshare.services.limit_up import LimitDownService, LimitUpService
from adshare.services.market_data import MarketDataService

logger = get_logger(__name__)
router = APIRouter(tags=["stock-data"])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _parse_date_str(date_str: Optional[str]) -> Optional[int]:
    """Parse a YYYYMMDD string to int."""
    if not date_str:
        return None
    try:
        return int(date_str)
    except (ValueError, TypeError):
        return None


def _codes_from_param(ts_code: Optional[str]) -> list[str]:
    """Split comma-separated TS codes."""
    if not ts_code:
        return []
    return [c.strip() for c in ts_code.split(",") if c.strip()]


# ------------------------------------------------------------------
# Stock Basic
# ------------------------------------------------------------------

@router.get("/stock_basic")
async def get_stock_basic(
    ts_code: Optional[str] = Query(default=None, description="TS code, e.g. 000001.SZ"),
    name: Optional[str] = Query(default=None, description="Stock name fuzzy match"),
    exchange: Optional[str] = Query(default=None, description="Exchange: SSE/SZSE/BSE"),
    market: Optional[str] = Query(default=None, description="Market type"),
    is_hs: Optional[str] = Query(default=None, description="HSC: N/H/S"),
    list_status: Optional[str] = Query(default=None, description="L/D/P"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get stock basic information."""
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        # Query all codes from warehouse
        df = warehouse.query_codes()
        if df.empty:
            return build_response(data=to_fields_items(pd.DataFrame()))

        # Apply filters
        if ts_code:
            codes = _codes_from_param(ts_code)
            if "code" in df.columns:
                df = df[df["code"].isin(codes)]

        if name and "name" in df.columns:
            df = df[df["name"].astype(str).str.contains(name, na=False)]

        if market and "board" in df.columns:
            df = df[df["board"].astype(str) == market]

        if list_status and "is_listed" in df.columns:
            want_listed = list_status == "L"
            df = df[df["is_listed"] == want_listed]

        if exchange and "code" in df.columns:
            suffix_map = {"SSE": ".SH", "SZSE": ".SZ", "BSE": ".BJ"}
            want_suffix = suffix_map.get(exchange.upper(), "")
            if want_suffix:
                df = df[df["code"].astype(str).str.endswith(want_suffix)]

        # Map to Pro platform fields
        df = map_stock_basic_fields(df)

        # Filter requested fields
        df = filter_fields(df, fields)

        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("stock_basic failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Trade Calendar
# ------------------------------------------------------------------

@router.get("/trade_cal")
async def get_trade_cal(
    exchange: Optional[str] = Query(default=None, description="Exchange: SSE/SZSE/BSE"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    is_open: Optional[str] = Query(default=None, description="1=open, 0=closed"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get trading calendar."""
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        # Map exchange to market code
        market = None
        if exchange:
            exchange_map = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}
            market = exchange_map.get(exchange.upper(), exchange.upper())

        df = warehouse.query_calendar(
            market=market,
            begin_date=_parse_date_str(start_date),
            end_date=_parse_date_str(end_date),
        )
        if df.empty:
            return build_response(data=to_fields_items(pd.DataFrame()))

        # Map to Pro platform fields
        df = map_trade_cal_fields(df)

        # Filter by is_open if requested
        if is_open is not None:
            want_open = int(is_open) == 1
            df = df[df["is_open"] == (1 if want_open else 0)]

        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("trade_cal failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Daily / Weekly / Monthly
# ------------------------------------------------------------------

def _get_kline_data(
    ts_code: Optional[str],
    trade_date: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    period: str,
    fields: Optional[str],
    service: MarketDataService,
) -> dict:
    """Shared logic for daily/weekly/monthly endpoints."""
    codes = _codes_from_param(ts_code)
    if not codes:
        return build_error_response("ts_code is required")

    # Resolve date range
    td = _parse_date_str(trade_date)
    sd = _parse_date_str(start_date)
    ed = _parse_date_str(end_date)

    if td is not None:
        sd = td
        ed = td
    elif sd is None and ed is None:
        # No dates provided — use a wide range (last 365 days default)
        from datetime import datetime, timedelta
        ed = int(datetime.now().strftime("%Y%m%d"))
        sd = int((datetime.now() - timedelta(days=365)).strftime("%Y%m%d"))
    elif sd is None:
        sd = 19900101
    elif ed is None:
        from datetime import datetime
        ed = int(datetime.now().strftime("%Y%m%d"))

    result = service.get_kline(
        codes=codes,
        begin_date=sd,
        end_date=ed,
        period=period,
        source="auto",
    )
    df = result.df
    if df is None or df.empty:
        return build_response(data=to_fields_items(pd.DataFrame()))

    # Compute derived fields
    df = compute_price_changes(df)
    df = convert_volume_to_lots(df)
    df = map_kline_fields(df)
    df = filter_fields(df, fields)

    return build_response(data=to_fields_items(df))


@router.get("/daily")
async def get_daily(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Get daily K-line data."""
    try:
        return _get_kline_data(ts_code, trade_date, start_date, end_date, "day", fields, service)
    except Exception as e:
        logger.error("daily failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/weekly")
async def get_weekly(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Get weekly K-line data."""
    try:
        return _get_kline_data(ts_code, trade_date, start_date, end_date, "week", fields, service)
    except Exception as e:
        logger.error("weekly failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/monthly")
async def get_monthly(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Get monthly K-line data."""
    try:
        return _get_kline_data(ts_code, trade_date, start_date, end_date, "month", fields, service)
    except Exception as e:
        logger.error("monthly failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Adj Factor
# ------------------------------------------------------------------

@router.get("/adj_factor")
async def get_adj_factor(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get adjustment factor data."""
    try:
        codes = _codes_from_param(ts_code)
        if not codes:
            return build_error_response("ts_code is required")

        td = _parse_date_str(trade_date)
        sd = _parse_date_str(start_date)
        ed = _parse_date_str(end_date)

        if td is not None:
            sd = td
            ed = td
        elif sd is None and ed is None:
            from datetime import datetime, timedelta
            ed = int(datetime.now().strftime("%Y%m%d"))
            sd = int((datetime.now() - timedelta(days=365)).strftime("%Y%m%d"))
        elif sd is None:
            sd = 19900101
        elif ed is None:
            from datetime import datetime
            ed = int(datetime.now().strftime("%Y%m%d"))

        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = warehouse.query_kline(codes=codes, begin_date=sd, end_date=ed, period="day")
        if df.empty:
            return build_response(data=to_fields_items(pd.DataFrame()))

        # Keep only adj_factor columns
        df = df[["code", "date", "adj_factor"]]
        df = map_adj_factor_fields(df)
        df = filter_fields(df, fields)

        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("adj_factor failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Pro Bar (Universal Bar with adj + MA)
# ------------------------------------------------------------------

@router.get("/pro_bar")
async def get_pro_bar(
    ts_code: str = Query(..., description="TS code"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    asset: Optional[str] = Query(default="E", description="E=stock, I=index"),
    adj: Optional[str] = Query(default=None, description="None/qfq/hfq"),
    freq: Optional[str] = Query(default="D", description="D/W/M"),
    ma: Optional[str] = Query(default=None, description="Moving averages, e.g. 5,10,20"),
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
):
    """Get universal bar data with optional adjustment and moving averages."""
    try:
        codes = _codes_from_param(ts_code)
        if not codes:
            return build_error_response("ts_code is required")

        # Resolve period from freq
        freq_map = {"D": "day", "W": "week", "M": "month"}
        period = freq_map.get(freq.upper(), "day") if freq else "day"

        # Resolve date range
        sd = _parse_date_str(start_date)
        ed = _parse_date_str(end_date)
        if sd is None and ed is None:
            from datetime import datetime, timedelta
            ed = int(datetime.now().strftime("%Y%m%d"))
            sd = int((datetime.now() - timedelta(days=365)).strftime("%Y%m%d"))
        elif sd is None:
            sd = 19900101
        elif ed is None:
            from datetime import datetime
            ed = int(datetime.now().strftime("%Y%m%d"))

        result = service.get_kline(
            codes=codes,
            begin_date=sd,
            end_date=ed,
            period=period,
            source="auto",
        )
        df = result.df
        if df is None or df.empty:
            return build_response(data=to_fields_items(pd.DataFrame()))

        # Apply adjustment if requested
        if adj and adj.lower() in ("qfq", "hfq"):
            adj_result = service.get_kline(
                codes=codes,
                begin_date=sd,
                end_date=ed,
                period=period,
                source="auto",
            )
            adj_df = adj_result.df
            if adj_df is not None and not adj_df.empty and "adj_factor" in adj_df.columns:
                df = apply_adjustment(df, adj_df[["date", "adj_factor"]], adj.lower())

        # Compute price changes
        df = compute_price_changes(df)
        df = convert_volume_to_lots(df)

        # Compute moving averages if requested
        if ma:
            try:
                ma_params = [int(x.strip()) for x in ma.split(",") if x.strip().isdigit()]
                if ma_params:
                    df = compute_moving_averages(df, ma_params)
            except Exception as e:
                logger.warning("MA calculation failed: %s", e)

        df = map_kline_fields(df)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("pro_bar failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Suspend
# ------------------------------------------------------------------

@router.get("/suspend_d")
async def get_suspend_d(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get suspension records derived from K-line data."""
    try:
        codes = _codes_from_param(ts_code)
        if not codes:
            return build_error_response("ts_code is required")

        td = _parse_date_str(trade_date)
        sd = _parse_date_str(start_date)
        ed = _parse_date_str(end_date)

        if td is not None:
            sd = td
            ed = td
        elif sd is None and ed is None:
            from datetime import datetime, timedelta
            ed = int(datetime.now().strftime("%Y%m%d"))
            sd = int((datetime.now() - timedelta(days=365 * 3)).strftime("%Y%m%d"))
        elif sd is None:
            sd = 19900101
        elif ed is None:
            from datetime import datetime
            ed = int(datetime.now().strftime("%Y%m%d"))

        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = warehouse.query_kline(codes=codes, begin_date=sd, end_date=ed, period="day")
        if df.empty:
            return build_response(data=to_fields_items(pd.DataFrame()))

        df = derive_suspensions(df)
        df = map_suspend_fields(df)
        df = filter_fields(df, fields)

        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("suspend_d failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Limit List
# ------------------------------------------------------------------

@router.get("/limit_list")
async def get_limit_list(
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    limit: Optional[str] = Query(default=None, description="U=up, D=down"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    up_service: LimitUpService = Depends(deps.get_limit_up_service_dep),
    down_service: LimitDownService = Depends(deps.get_limit_down_service_dep),
):
    """Get limit-up/down list in Pro platform format."""
    try:
        td = _parse_date_str(trade_date)
        if td is None:
            from datetime import datetime
            td = int(datetime.now().strftime("%Y%m%d"))

        up_items = []
        down_items = []

        if limit is None or limit.upper() in ("U", "UP", ""):
            up_resp = up_service.get_limit_up(date=td, exclude_st=True)
            up_items = getattr(up_resp, "stocks", []) or []

        if limit is None or limit.upper() in ("D", "DOWN"):
            down_resp = down_service.get_limit_down(date=td, exclude_st=True)
            down_items = getattr(down_resp, "stocks", []) or []

        # Filter by ts_code if requested
        if ts_code:
            codes = set(_codes_from_param(ts_code))
            up_items = [item for item in up_items if getattr(item, "code", "") in codes]
            down_items = [item for item in down_items if getattr(item, "code", "") in codes]

        df = build_limit_list(up_items, down_items, td)
        df = filter_fields(df, fields)

        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("limit_list failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# New Share
# ------------------------------------------------------------------

@router.get("/new_share")
async def get_new_share(
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get new shares listed within a date range."""
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        sd = _parse_date_str(start_date)
        ed = _parse_date_str(end_date)
        if sd is None and ed is None:
            from datetime import datetime, timedelta
            ed = int(datetime.now().strftime("%Y%m%d"))
            sd = int((datetime.now() - timedelta(days=90)).strftime("%Y%m%d"))
        elif sd is None:
            sd = ed
        elif ed is None:
            ed = sd

        df = warehouse.query_codes(is_listed=True)
        if df.empty:
            return build_response(data=to_fields_items(pd.DataFrame()))

        df = map_stock_basic_fields(df)
        df = filter_new_shares(df, sd)
        df["list_date_int"] = pd.to_numeric(df["ipo_date"], errors="coerce").fillna(0).astype(int)
        df = df[(df["list_date_int"] >= sd) & (df["list_date_int"] <= ed)]
        df = df.drop(columns=["list_date_int"], errors="ignore")

        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("new_share failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Financial Statements
# ------------------------------------------------------------------

FINANCIAL_FIELDS = {
    "income": [
        "ts_code", "ann_date", "f_ann_date", "end_date", "comp_type", "basic_eps",
        "diluted_eps", "total_revenue", "revenue", "int_income", "prem_earned",
        "comm_income", "n_commis_income", "n_oth_income", "n_oth_b_income",
        "prem_income", "out_prem", "une_prem_reser", "reins_income",
        "n_sec_tb_income", "n_sec_uw_income", "n_asset_mg_income", "oth_b_income",
        "fv_value_chg_gain", "invest_income", "ass_invest_income", "tot_opcost",
        "oper_cost", "int_exp", "comm_exp", "biz_tax_surchg", "sell_exp",
        "admin_exp", "fin_exp", "assets_impair_loss", "prem_refund",
        "compens_payout", "reser_insur_liab", "div_payt", "reins_exp",
        "oper_exp", "compens_payout_refu", "insur_reser_refu", "reins_cost_refund",
        "other_bus_cost", "operate_profit", "non_oper_income", "non_oper_exp",
        "nca_disploss", "tot_profit", "income_tax", "n_income",
        "n_income_attr_p", "minority_gain", "oth_compr_income", "t_compr_income",
        "compr_inc_attr_p", "compr_inc_attr_m_s", "ebit", "ebitda", "insurance_exp",
        "undist_profit", "distable_profit",
    ],
    "balance": [
        "ts_code", "ann_date", "f_ann_date", "end_date", "comp_type", "total_share",
        "cap_rese", "undistr_porfit", "surplus_rese", "special_rese", "money_cap",
        "trad_asset", "notes_receiv", "accounts_receiv", "oth_receiv", "prepayment",
        "div_receiv", "int_receiv", "inventories", "amor_exp", "nca_within_1y",
        "sett_rsrv", "loanto_oth_bank_fi", "premium_receiv", "reinsur_receiv",
        "reinsur_res_receiv", "pur_resale_fa", "oth_cur_assets", "total_cur_assets",
        "fa_avail_for_sale", "htm_invest", "lt_eqt_invest", "invest_real_estate",
        "time_deposits", "oth_assets", "lt_rec", "fix_assets", "cip", "const_materials",
        "fixed_assets_disp", "produ_bio_assets", "oil_and_gas_assets", "intan_assets",
        "lt_amor_exp", "defer_tax_assets", "decr_in_disbur", "oth_nca",
        "total_nca", "cash_reser_cb", "depos_in_oth_bfi", "prec_metals",
        "deriv_assets", "total_assets", "lt_borr", "st_borr", "cb_borr",
        "depos", "loan_oth_bank", "trading_fl", "notes_payable", "acct_payable",
        "adv_receipts", "sold_for_repur_fa", "comm_payable", "payroll_payable",
        "taxes_payable", "int_payable", "div_payable", "oth_payable",
        "acc_exp", "deferred_inc", "st_bonds_payable", "payable_to_reinsurer",
        "rsrv_insur_cont", "acting_trading_sec", "acting_uw_sec", "non_cur_liab_due_1y",
        "oth_cur_liab", "total_cur_liab", "bond_payable", "lt_payable",
        "specific_payable", "estimated_liab", "defer_tax_liab", "defer_revenu",
        "oth_nca", "total_nca", "depos_oth_bfi", "deriv_liab", "depos",
        "agency_bus_liab", "oth_liab", "prem_received", "reinsur_cont",
        "total_liab", "general_risk_reser", "undist_profit", "appropriated_reture",
        "foreign_curr_cap_reser", "mom_eqt", "cap_rese", "surplus_rese",
        "oper_revenue", "treasury_share", "ordin_risk_reser", "forex_differ",
        "invest_uncert_loss", "minority_int", "total_hldr_eqy_exc_min_int",
        "total_hldr_eqy_inc_min_int", "total_liab_hldr_eqy", "lt_payroll_payable",
        "oth_comp_income", "oth_eqt_tools", "oth_eqt_tools_p_shr", "lending_funds",
        "acc_receivable", "st_fin_payable", "payables", "hfs_assets", "hfs_sales",
    ],
    "cashflow": [
        "ts_code", "ann_date", "f_ann_date", "end_date", "comp_type", "net_profit",
        "finan_exp", "c_fr_sale_sg", "recp_tax_rends", "n_depos_incr_fi",
        "n_incr_loans_cb", "n_inc_borr_oth_fi", "prem_fr_orig_contr", "n_incr_insured_dep",
        "n_reinsur_prem", "n_incr_disp_tfa", "ifc_cash_incr", "n_incr_disp_faas",
        "n_incr_loans_oth_bank", "n_cap_incr_repur", "c_fr_oth_operate_a", "c_inf_fr_operate_a",
        "c_paid_goods_s", "c_paid_to_for_empl", "c_paid_for_taxes", "n_incr_clt_loan_adv",
        "n_incr_dep_cb", "c_pay_claims_orig_inco", "pay_handling_chrg", "pay_comm_insur_plcy",
        "oth_cash_pay_oper_act", "st_cash_out_act", "n_cashflow_act", "oth_recp_ral_inv_act",
        "c_disp_withdrwl_invest", "c_recp_return_invest", "n_recp_disp_fiolta",
        "n_recp_disp_sobu", "stot_inflows_inv_act", "c_pay_acq_const_fiolta",
        "c_paid_invest", "n_disp_subs_oth_biz", "oth_pay_ral_inv_act", "n_incr_pledge_loan",
        "stot_out_inv_act", "n_cashflow_inv_act", "c_recp_borrow", "proc_issue_bonds",
        "oth_cash_recp_ral_fnc_act", "stot_cash_in_fnc_act", "free_cashflow",
        "c_prepay_amt_borr", "c_pay_dist_dcpint_profits", "c_other_cash_pay_ral_fnc_act",
        "stot_cashout_fnc_act", "n_cash_flows_fnc_act", "eff_fx_flu_cash", "n_incr_cash_cash_equ",
        "c_cash_equ_beg_period", "c_cash_equ_end_period", "c_recp_cap_contrib",
        "incl_cash_rec_borrowings", "incl_dvd_profit_paid_sc_ms", "unkown_cash_item",
        "fin_end_cash_bal", "im_net_cashflow_oper_act", "defect_tax_for_losses",
        "net_cash_after_oper", "loan_and_advance", " leasing_and_deposit",
    ],
}


def _get_financial_empty(statement_type: str) -> pd.DataFrame:
    """Return empty DataFrame with Pro platform financial statement columns."""
    fields = FINANCIAL_FIELDS.get(statement_type, [])
    return pd.DataFrame(columns=fields)


@router.get("/income")
async def get_income(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    ann_date: Optional[str] = Query(default=None, description="Announcement date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    period: Optional[str] = Query(default=None, description="Report period YYYYMMDD"),
    report_type: Optional[str] = Query(default=None, description="Report type"),
    comp_type: Optional[str] = Query(default=None, description="Company type"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get income statement data (Pro platform format).

    Data is populated by the worker service to warehouse.reference.income.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        sd = _parse_date_str(start_date) or _parse_date_str(ann_date) or _parse_date_str(period)
        ed = _parse_date_str(end_date) or sd
        df = warehouse.query_financial("income", ts_code=ts_code, begin_date=sd, end_date=ed)
        if df.empty:
            df = _get_financial_empty("income")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("income failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/balance_sheet")
async def get_balance_sheet(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    ann_date: Optional[str] = Query(default=None, description="Announcement date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    period: Optional[str] = Query(default=None, description="Report period YYYYMMDD"),
    report_type: Optional[str] = Query(default=None, description="Report type"),
    comp_type: Optional[str] = Query(default=None, description="Company type"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get balance sheet data (Pro platform format).

    Data is populated by the worker service to warehouse.reference.balance_sheet.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        sd = _parse_date_str(start_date) or _parse_date_str(ann_date) or _parse_date_str(period)
        ed = _parse_date_str(end_date) or sd
        df = warehouse.query_financial("balance_sheet", ts_code=ts_code, begin_date=sd, end_date=ed)
        if df.empty:
            df = _get_financial_empty("balance")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("balance_sheet failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cashflow")
async def get_cashflow(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    ann_date: Optional[str] = Query(default=None, description="Announcement date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    period: Optional[str] = Query(default=None, description="Report period YYYYMMDD"),
    report_type: Optional[str] = Query(default=None, description="Report type"),
    comp_type: Optional[str] = Query(default=None, description="Company type"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get cash flow statement data (Pro platform format).

    Data is populated by the worker service to warehouse.reference.cashflow.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        sd = _parse_date_str(start_date) or _parse_date_str(ann_date) or _parse_date_str(period)
        ed = _parse_date_str(end_date) or sd
        df = warehouse.query_financial("cashflow", ts_code=ts_code, begin_date=sd, end_date=ed)
        if df.empty:
            df = _get_financial_empty("cashflow")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("cashflow failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Financial Indicator
# ------------------------------------------------------------------

FINA_INDICATOR_FIELDS = [
    "ts_code", "ann_date", "end_date", "eps", "dt_eps", "total_revenue_ps",
    "revenue_ps", "capital_rese_ps", "surplus_rese_ps", "undist_profit_ps",
    "extra_item", "profit_dedt", "gross_margin", "current_ratio", "quick_ratio",
    "cash_ratio", "invturn_days", "arturn_days", "inv_turn", "ar_turn",
    "ca_turn", "fa_turn", "assets_turn", "op_income", "valuechange_income",
    "interst_income", "daa", "ebit", "ebitda", "fcff", "fcfe", "current_exint",
    "noncurrent_exint", "interestdebt", "netdebt", "tangible_asset", "working_capital",
    "networking_capital", "invest_capital", "retained_earnings", "diluted2_eps",
    "bps", "ocfps", "retainedps", "cfps", "ebit_ps", "fcff_ps", "fcfe_ps",
    "netprofit_margin", "grossprofit_margin", "cogs_of_sales", "expense_of_sales",
    "profit_to_gr", "saleexp_to_gr", "adminexp_of_gr", "finaexp_of_gr",
    "impai_ttm", "gc_of_gr", "op_of_gr", "ebit_of_gr", "roe", "roe_waa",
    "roe_dt", "roa", "npta", "roic", "roe_yearly", "roa2_yearly", "roe_avg",
    "opincome_of_ebt", "investincome_of_ebt", "non_op_profit", "tax_to_ebt",
    "dtprofit_to_profit", "salescash_to_or", "ocf_to_or", "ocf_to_opincome",
    "capitalized_to_da", "debt_to_assets", "assets_to_eqt", "dp_assets_to_eqt",
    "ca_to_assets", "nca_to_assets", "tbassets_to_totalassets", "int_to_talcap",
    "eqt_to_talcapital", "currentdebt_to_debt", "longdeb_to_debt", "ocf_to_shortdebt",
    "debt_to_eqt", "eqt_to_debt", "eqt_to_interestdebt", "tangibleasset_to_debt",
    "tangasset_to_intdebt", "tangibleasset_to_netdebt", "ocf_to_debt",
    "ocf_to_interestdebt", "ocf_to_netdebt", "ebit_to_interest", "longdebt_to_workingcapital",
    "ebitda_to_debt", "turn_days", "roa_yearly", "roa_dp", "fixed_assets",
    "profit_prefin_exp", "non_op_profit", "op_to_ebt", "nop_to_ebt", "ocf_to_profit",
    "cash_to_liqdebt", "cash_to_liqdebt_withinterest", "op_to_liqdebt",
    "op_to_debt", "roic_yearly", "total_fa_trun", "profit_to_op", "q_opincome",
    "q_investincome", "q_dtprofit", "q_eps", "q_netprofit_margin", "q_gsprofit_margin",
    "q_exp_to_sales", "q_profit_to_sales", "q_saleexp_to_gr", "q_adminexp_to_gr",
    "q_finaexp_to_gr", "q_impair_to_gr_ttm", "q_gc_to_gr", "q_op_to_gr",
    "q_roe", "q_dt_roe", "q_total_fa_turn", "q_op_roe", "q_basic_eps", "q_dt_eps",
    "basic_eps_yoy", "dt_eps_yoy", "cfps_yoy", "op_yoy", "ebt_yoy", "netprofit_yoy",
    "dt_netprofit_yoy", "ocf_yoy", "roe_yoy", "bps_yoy", "assets_yoy", "eqt_yoy",
    "tr_yoy", "or_yoy", "q_sales_yoy", "q_op_qoq", "q_profit_qoq", "q_netprofit_qoq",
    "equity_yoy", "rd_exp",
]


@router.get("/fina_indicator")
async def get_fina_indicator(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    ann_date: Optional[str] = Query(default=None, description="Announcement date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    period: Optional[str] = Query(default=None, description="Report period YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get financial indicator data (Pro platform format).

    Data is populated by the worker service; returns empty until synchronized.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = pd.DataFrame(columns=FINA_INDICATOR_FIELDS)
        logger.info("fina_indicator data not yet populated in warehouse")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("fina_indicator failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Daily Basic
# ------------------------------------------------------------------

DAILY_BASIC_FIELDS = [
    "ts_code", "trade_date", "close", "turnover_rate", "turnover_rate_f",
    "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm",
    "total_share", "float_share", "free_share", "total_mv", "circ_mv",
]


def _build_daily_basic(kline_df: pd.DataFrame, codes_df: pd.DataFrame) -> pd.DataFrame:
    """Build daily_basic from K-line and code metadata.

    Only turnover_rate can be computed when float_share is available.
    Other valuation fields require financial data not yet in warehouse.
    """
    if kline_df is None or kline_df.empty:
        return pd.DataFrame(columns=DAILY_BASIC_FIELDS)

    df = kline_df.copy()
    df = df.rename(columns={"code": "ts_code", "date": "trade_date"})
    df["ts_code"] = df["ts_code"].astype(str)
    df["trade_date"] = pd.to_numeric(df["trade_date"], errors="coerce").fillna(0).astype(int)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

    # Build code -> share maps if available
    float_share_map = {}
    total_share_map = {}
    if codes_df is not None and not codes_df.empty and "code" in codes_df.columns:
        for _, row in codes_df.iterrows():
            code = str(row.get("code", ""))
            if not code:
                continue
            # Try common column names for share counts (in 万股)
            fs = row.get("float_share") or row.get(" negotiable_share") or row.get("a_share") or None
            ts = row.get("total_share") or row.get("total_capital") or None
            if fs is not None:
                try:
                    float_share_map[code] = float(fs)
                except Exception:
                    pass
            if ts is not None:
                try:
                    total_share_map[code] = float(ts)
                except Exception:
                    pass

    def _turnover(row):
        vol = row.get("volume", 0) or 0
        fs = float_share_map.get(row.get("ts_code"), 0)
        if fs and fs > 0:
            # volume is shares, float_share is 万股 -> convert volume to 万股
            return round(vol / 10000 / fs * 100, 4)
        return None

    df["turnover_rate"] = df.apply(_turnover, axis=1)
    df["turnover_rate_f"] = df["turnover_rate"]

    def _volume_ratio(row):
        # Placeholder: requires average volume of previous 5 days
        return None

    df["volume_ratio"] = df.apply(_volume_ratio, axis=1)

    # Valuation fields: not available without financial data
    for col in ["pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm"]:
        df[col] = None

    df["total_share"] = df["ts_code"].map(total_share_map)
    df["float_share"] = df["ts_code"].map(float_share_map)
    df["free_share"] = None

    def _mv(row):
        close = row.get("close") or 0
        ts = row.get("total_share") or 0
        fs = row.get("float_share") or 0
        return (
            round(close * ts * 10000, 2) if close and ts else None,
            round(close * fs * 10000, 2) if close and fs else None,
        )

    mv = df.apply(_mv, axis=1)
    df["total_mv"] = mv.apply(lambda x: x[0])
    df["circ_mv"] = mv.apply(lambda x: x[1])

    return df[DAILY_BASIC_FIELDS]


@router.get("/daily_basic")
async def get_daily_basic(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get daily basic indicators (Pro platform format).

    Turnover and market cap are computed from K-line + code metadata when
    share counts are available. Valuation fields (PE/PB/PS) require financial
    data not yet synchronized to the warehouse.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        codes = _codes_from_param(ts_code)
        if not codes:
            return build_error_response("ts_code is required")

        td = _parse_date_str(trade_date)
        sd = _parse_date_str(start_date)
        ed = _parse_date_str(end_date)

        if td is not None:
            sd = td
            ed = td
        elif sd is None and ed is None:
            from datetime import datetime, timedelta
            ed = int(datetime.now().strftime("%Y%m%d"))
            sd = int((datetime.now() - timedelta(days=30)).strftime("%Y%m%d"))
        elif sd is None:
            sd = 19900101
        elif ed is None:
            from datetime import datetime
            ed = int(datetime.now().strftime("%Y%m%d"))

        kline_df = warehouse.query_kline(codes=codes, begin_date=sd, end_date=ed, period="day")
        codes_df = warehouse.query_codes()

        df = _build_daily_basic(kline_df, codes_df)
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("daily_basic failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Index Data
# ------------------------------------------------------------------

INDEX_BASIC_FIELDS = [
    "ts_code", "name", "fullname", "market", "publisher", "index_type",
    "category", "base_date", "base_point", "list_date", "weight_rule",
    "desc", "exp_date",
]


@router.get("/index_basic")
async def get_index_basic(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    name: Optional[str] = Query(default=None, description="Index name fuzzy match"),
    market: Optional[str] = Query(default=None, description="Market: SZ/SH/CSI"),
    publisher: Optional[str] = Query(default=None, description="Publisher"),
    category: Optional[str] = Query(default=None, description="Category"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get index basic information (Pro platform format).

    Data is populated by the worker service; returns empty until synchronized.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = pd.DataFrame(columns=INDEX_BASIC_FIELDS)
        logger.info("index_basic data not yet populated in warehouse")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("index_basic failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


INDEX_DAILY_FIELDS = [
    "ts_code", "trade_date", "close", "open", "high", "low", "pre_close",
    "change", "pct_chg", "vol", "amount",
]


@router.get("/index_daily")
async def get_index_daily(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get index daily K-line (Pro platform format).

    Data is populated by the worker service; returns empty until synchronized.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = pd.DataFrame(columns=INDEX_DAILY_FIELDS)
        logger.info("index_daily data not yet populated in warehouse")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("index_daily failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


INDEX_MEMBER_FIELDS = [
    "index_code", "index_name", "con_code", "con_name", "in_date", "out_date",
    "is_new",
]


@router.get("/index_member")
async def get_index_member(
    index_code: Optional[str] = Query(default=None, description="Index TS code"),
    ts_code: Optional[str] = Query(default=None, description="Constituent TS code"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get index constituent stocks (Pro platform format).

    Data is populated by the worker service to warehouse.reference.index_member.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = warehouse.query_index_member(index_code=index_code, ts_code=ts_code)
        if df.empty:
            df = pd.DataFrame(columns=INDEX_MEMBER_FIELDS)
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("index_member failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


INDEX_WEIGHT_FIELDS = [
    "index_code", "con_code", "trade_date", "weight",
]


@router.get("/index_weight")
async def get_index_weight(
    index_code: Optional[str] = Query(default=None, description="Index TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get index constituent weights (Pro platform format).

    Data is populated by the worker service; returns empty until synchronized.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = pd.DataFrame(columns=INDEX_WEIGHT_FIELDS)
        logger.info("index_weight data not yet populated in warehouse")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("index_weight failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Market Reference
# ------------------------------------------------------------------

MONEYFLOW_FIELDS = [
    "ts_code", "trade_date", "buy_sm_vol", "buy_sm_amount", "sell_sm_vol",
    "sell_sm_amount", "buy_md_vol", "buy_md_amount", "sell_md_vol",
    "sell_md_amount", "buy_lg_vol", "buy_lg_amount", "sell_lg_vol",
    "sell_lg_amount", "buy_elg_vol", "buy_elg_amount", "sell_elg_vol",
    "sell_elg_amount", "net_mf_vol", "net_mf_amount",
]


@router.get("/moneyflow")
async def get_moneyflow(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get stock money flow data (Pro platform format).

    Data is populated by the worker service; returns empty until synchronized.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = pd.DataFrame(columns=MONEYFLOW_FIELDS)
        logger.info("moneyflow data not yet populated in warehouse")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("moneyflow failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


MARGIN_FIELDS = [
    "trade_date", "exchange_id", "rzye", "rzmre", "rzche", "rqye", "rqmcl",
    "rqchl", "rzrqye", "rqyl",
]


@router.get("/margin")
async def get_margin(
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    exchange_id: Optional[str] = Query(default=None, description="Exchange ID"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get margin trading summary (Pro platform format).

    Data is populated by the worker service; returns empty until synchronized.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = pd.DataFrame(columns=MARGIN_FIELDS)
        logger.info("margin data not yet populated in warehouse")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("margin failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


MARGIN_DETAIL_FIELDS = [
    "ts_code", "trade_date", "rzye", "rqyl", "rqylts", "rzmre", "rzche",
    "rqmcl", "rqchl", "rzrqye",
]


@router.get("/margin_detail")
async def get_margin_detail(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get margin trading detail per stock (Pro platform format).

    Data is populated by the worker service; returns empty until synchronized.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = pd.DataFrame(columns=MARGIN_DETAIL_FIELDS)
        logger.info("margin_detail data not yet populated in warehouse")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("margin_detail failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


TOP_LIST_FIELDS = [
    "ts_code", "trade_date", "name", "close", "pct_change", "turnover_rate",
    "amount", "l_buy", "l_sell", "net_amount", "amount_prop", "l_buy_prop",
    "l_sell_prop", "reason",
]


@router.get("/top_list")
async def get_top_list(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get top list (dragon tiger list) data (Pro platform format).

    Data is populated by the worker service; returns empty until synchronized.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = pd.DataFrame(columns=TOP_LIST_FIELDS)
        logger.info("top_list data not yet populated in warehouse")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("top_list failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


TOP_INST_FIELDS = [
    "ts_code", "trade_date", "name", "exalter", "buy", "buy_rate", "sell",
    "sell_rate", "net_buy", "side",
]


@router.get("/top_inst")
async def get_top_inst(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get top institution trading detail (Pro platform format).

    Data is populated by the worker service; returns empty until synchronized.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = pd.DataFrame(columns=TOP_INST_FIELDS)
        logger.info("top_inst data not yet populated in warehouse")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("top_inst failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


BLOCK_TRADE_FIELDS = [
    "ts_code", "trade_date", "price", "vol", "amount", "buyer", "seller",
]


@router.get("/block_trade")
async def get_block_trade(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    trade_date: Optional[str] = Query(default=None, description="Trade date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get block trade data (Pro platform format).

    Data is populated by the worker service; returns empty until synchronized.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = pd.DataFrame(columns=BLOCK_TRADE_FIELDS)
        logger.info("block_trade data not yet populated in warehouse")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("block_trade failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Shareholder / Name Change
# ------------------------------------------------------------------

STK_HOLDERNUMBER_FIELDS = [
    "ts_code", "ann_date", "end_date", "holder_num", "holder_num_change",
    "holder_num_change_rate", "avg_hold_num", "avg_hold_ratio", "hold_num_sum",
]


@router.get("/stk_holdernumber")
async def get_stk_holdernumber(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    enddate: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get shareholder number data (Pro platform format).

    Data is populated by the worker service to warehouse.reference.stk_holdernumber.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        sd = _parse_date_str(start_date) or _parse_date_str(enddate)
        ed = _parse_date_str(end_date) or _parse_date_str(enddate) or sd
        df = warehouse.query_shareholder(ts_code=ts_code, begin_date=sd, end_date=ed)
        if df.empty:
            df = pd.DataFrame(columns=STK_HOLDERNUMBER_FIELDS)
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("stk_holdernumber failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


NAMECHANGE_FIELDS = [
    "ts_code", "name", "start_date", "end_date", "ann_date", "change_reason",
]


@router.get("/namechange")
async def get_namechange(
    ts_code: Optional[str] = Query(default=None, description="TS code"),
    start_date: Optional[str] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[str] = Query(default=None, description="End date YYYYMMDD"),
    fields: Optional[str] = Query(default=None, description="Comma-separated fields"),
    warehouse: Optional[HistoricalWarehouse] = Depends(deps.get_warehouse_dep),
):
    """Get stock name change history (Pro platform format).

    Data is populated by the worker service; returns empty until synchronized.
    """
    try:
        if warehouse is None:
            return build_error_response("Historical warehouse is disabled")

        df = pd.DataFrame(columns=NAMECHANGE_FIELDS)
        logger.info("namechange data not yet populated in warehouse")
        df = filter_fields(df, fields)
        return build_response(data=to_fields_items(df))
    except Exception as e:
        logger.error("namechange failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
