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
# Daily Basic
# ============================================================


class DailyBasicParams(BaseModel):
    """Query parameters for /daily_basic."""

    ts_code: Optional[str] = Field(default=None, description="TS code")
    trade_date: Optional[str] = Field(default=None, description="Trade date YYYYMMDD")
    start_date: Optional[str] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="End date YYYYMMDD")
    fields: Optional[str] = Field(default=None, description="Comma-separated fields")
