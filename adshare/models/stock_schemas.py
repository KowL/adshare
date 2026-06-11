"""Pydantic schemas for Pro-style stock data API requests and responses."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ============================================================
# Base Pro-style Response
# ============================================================


class ProDataItem(BaseModel):
    """A single row of data as {field: value} dict (alternative format)."""

    pass


class ProDataPayload(BaseModel):
    """The data payload in {fields, items} format."""

    fields: List[str] = []
    items: List[List[Any]] = []


class ProDataResponse(BaseModel):
    """Standard Pro-style API response."""

    code: int = 0
    msg: str = "success"
    data: Optional[ProDataPayload] = None
    request_id: Optional[str] = None


class ProErrorResponse(BaseModel):
    """Standard Pro-style error response."""

    code: int = -1
    msg: str = "error"
    data: Optional[Any] = None
    request_id: Optional[str] = None


# ============================================================
# Stock Basic
# ============================================================


class StockBasicParams(BaseModel):
    """Query parameters for /stock_basic."""

    ts_code: Optional[str] = Field(default=None, description="TS code, e.g. 000001.SZ")
    name: Optional[str] = Field(default=None, description="Stock name fuzzy match")
    exchange: Optional[str] = Field(default=None, description="Exchange: SSE/SZSE/BSE")
    market: Optional[str] = Field(default=None, description="Market type")
    is_hs: Optional[str] = Field(default=None, description="HSC: N/H/S")
    list_status: Optional[str] = Field(default=None, description="L/D/P")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


# ============================================================
# Trade Calendar
# ============================================================


class TradeCalParams(BaseModel):
    """Query parameters for /trade_cal."""

    exchange: Optional[str] = Field(default=None, description="Exchange: SSE/SZSE/BSE")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    is_open: Optional[str] = Field(default=None, description="1=open, 0=closed")


# ============================================================
# Daily / Weekly / Monthly
# ============================================================


class DailyParams(BaseModel):
    """Query parameters for /daily."""

    ts_code: Optional[str] = Field(default=None, description="TS code, e.g. 000001.SZ")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class WeeklyParams(BaseModel):
    """Query parameters for /weekly."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class MonthlyParams(BaseModel):
    """Query parameters for /monthly."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


# ============================================================
# Adj Factor
# ============================================================


class AdjFactorParams(BaseModel):
    """Query parameters for /adj_factor."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


# ============================================================
# Pro Bar (Universal Bar)
# ============================================================


class ProBarParams(BaseModel):
    """Query parameters for /pro_bar."""

    ts_code: str = Field(description="TS code")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    asset: Optional[str] = Field(default="E", description="E=stock, I=index")
    adj: Optional[str] = Field(default=None, description="None/qfq/hfq")
    freq: Optional[str] = Field(default="D", description="D/W/M")
    ma: Optional[str] = Field(default=None, description="Moving averages, e.g. 5,10,20")


# ============================================================
# Suspend
# ============================================================


class SuspendDParams(BaseModel):
    """Query parameters for /suspend_d."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


# ============================================================
# Limit List
# ============================================================


class LimitListParams(BaseModel):
    """Query parameters for /limit_list."""

    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    ts_code: Optional[str] = Field(default=None, description="TS code")
    limit: Optional[str] = Field(default=None, description="U=up, D=down")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


# ============================================================
# New Share
# ============================================================


class NewShareParams(BaseModel):
    """Query parameters for /new_share."""

    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


# ============================================================
# Daily Basic
# ============================================================


class DailyBasicParams(BaseModel):
    """Query parameters for /daily_basic."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


# ============================================================
# Financial Statements
# ============================================================


class IncomeParams(BaseModel):
    """Query parameters for /income."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    ann_date: Optional[str] = Field(default=None, description="Announcement date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    period: Optional[str] = Field(default=None, description="Report period YYYYMMDD")
    report_type: Optional[str] = Field(default=None, description="Report type")
    comp_type: Optional[str] = Field(default=None, description="Company type")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class BalanceSheetParams(BaseModel):
    """Query parameters for /balance_sheet."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    ann_date: Optional[str] = Field(default=None, description="Announcement date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    period: Optional[str] = Field(default=None, description="Report period YYYYMMDD")
    report_type: Optional[str] = Field(default=None, description="Report type")
    comp_type: Optional[str] = Field(default=None, description="Company type")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class CashFlowParams(BaseModel):
    """Query parameters for /cashflow."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    ann_date: Optional[str] = Field(default=None, description="Announcement date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    period: Optional[str] = Field(default=None, description="Report period YYYYMMDD")
    report_type: Optional[str] = Field(default=None, description="Report type")
    comp_type: Optional[str] = Field(default=None, description="Company type")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


# ============================================================
# Financial Indicator
# ============================================================


class FinaIndicatorParams(BaseModel):
    """Query parameters for /fina_indicator."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    ann_date: Optional[str] = Field(default=None, description="Announcement date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    period: Optional[str] = Field(default=None, description="Report period YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")



# ============================================================
# Index Data
# ============================================================


class IndexBasicParams(BaseModel):
    """Query parameters for /index_basic."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    name: Optional[str] = Field(default=None, description="Index name fuzzy match")
    market: Optional[str] = Field(default=None, description="Market: SZ/SH/CSI")
    publisher: Optional[str] = Field(default=None, description="Publisher")
    category: Optional[str] = Field(default=None, description="Category")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class IndexDailyParams(BaseModel):
    """Query parameters for /index_daily."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class IndexMemberParams(BaseModel):
    """Query parameters for /index_member."""

    index_code: Optional[str] = Field(default=None, description="Index TS code")
    ts_code: Optional[str] = Field(default=None, description="Constituent TS code")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class IndexWeightParams(BaseModel):
    """Query parameters for /index_weight."""

    index_code: Optional[str] = Field(default=None, description="Index TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


# ============================================================
# Market Reference
# ============================================================


class MoneyFlowParams(BaseModel):
    """Query parameters for /moneyflow."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class MarginParams(BaseModel):
    """Query parameters for /margin."""

    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    exchange_id: Optional[str] = Field(default=None, description="Exchange ID")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class MarginDetailParams(BaseModel):
    """Query parameters for /margin_detail."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class TopListParams(BaseModel):
    """Query parameters for /top_list."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class TopInstParams(BaseModel):
    """Query parameters for /top_inst."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class BlockTradeParams(BaseModel):
    """Query parameters for /block_trade."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


# ============================================================
# Shareholder / Name Change
# ============================================================


class StkHoldernumberParams(BaseModel):
    """Query parameters for /stk_holdernumber."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    enddate: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")


class NamechangeParams(BaseModel):
    """Query parameters for /namechange."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")
