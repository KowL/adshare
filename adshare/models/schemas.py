"""Pydantic schemas for request/response validation."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ============================================================
# Base Response
# ============================================================


class BaseResponse(BaseModel):
    """Base API response."""

    success: bool = True
    message: Optional[str] = None
    cached: bool = False
    cached_at: Optional[datetime] = None


class DataResponse(BaseResponse):
    """Response with data payload."""

    count: int = 0
    data: Any = None


class ErrorResponse(BaseResponse):
    """Error response."""

    success: bool = False
    error_type: str = "unknown_error"
    suggestion: Optional[str] = None


# ============================================================
# Market Data
# ============================================================


class CodeListRequest(BaseModel):
    """Request for security code list."""

    security_type: str = Field(
        default="stock_a",
        description="Security type: stock_a, index_a, etf, ... "
        "(legacy EXTRA_* values are still accepted)",
    )


class CodeListResponse(DataResponse):
    """Response for code list."""

    security_type: str
    code_list: List[str] = []


class CalendarRequest(BaseModel):
    """Request for trading calendar."""

    market: str = Field(default="SH", description="Market code: SH, SZ, BJ, etc.")
    date: Optional[int] = Field(default=None, description="Query date YYYYMMDD")


class CalendarResponse(DataResponse):
    """Response for trading calendar."""

    market: str
    query_date: int
    calendar: List[int] = []


# ============================================================
# K-line Data
# ============================================================


class KlineRequest(BaseModel):
    """Request for K-line data."""

    codes: str = Field(description="Comma-separated stock codes, e.g. '000001.SZ,600000.SH'")
    begin_date: int = Field(description="Start date YYYYMMDD")
    end_date: int = Field(description="End date YYYYMMDD")
    period: str = Field(default="day", description="Period: day, week, month, min1, min5, etc.")
    limit: Optional[int] = Field(default=None, description="Max records to return")
    offset: int = Field(default=0, description="Records to skip")

    @field_validator("codes")
    @classmethod
    def validate_codes(cls, v: str) -> str:
        if not v:
            raise ValueError("codes cannot be empty")
        return v

    @field_validator("begin_date", "end_date")
    @classmethod
    def validate_date(cls, v: int) -> int:
        if not (10000000 <= v <= 99999999):
            raise ValueError("date must be YYYYMMDD format")
        return v


class KlineItem(BaseModel):
    """Single K-line record."""

    code: str
    date: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float


class KlineResponse(DataResponse):
    """Response for K-line data."""

    codes: List[str] = []
    period: str = "day"
    begin_date: int
    end_date: int
    limit: Optional[int] = None
    offset: int = 0
    data: List[KlineItem] = []


# ============================================================
# Snapshot Data
# ============================================================


class SnapshotRequest(BaseModel):
    """Request for snapshot data."""

    codes: str = Field(description="Comma-separated stock codes")
    date: Optional[int] = Field(default=None, description="Trade date YYYYMMDD")
    time: Optional[int] = Field(default=None, description="Trade time HHMMSS")


class SnapshotItem(BaseModel):
    """Single snapshot record."""

    code: str
    date: int
    time: Optional[int] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[int] = None
    amount: Optional[float] = None
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None


class SnapshotResponse(DataResponse):
    """Response for snapshot data."""

    data: List[SnapshotItem] = []


# ============================================================
# Stock Basic Info
# ============================================================


class StockBasicRequest(BaseModel):
    """Request for stock basic info."""

    codes: Optional[str] = Field(default=None, description="Comma-separated codes, empty for all")
    summary_only: bool = Field(default=False, description="Return summary only")


class StockBasicItem(BaseModel):
    """Single stock basic info."""

    code: str
    name: Optional[str] = None
    comp_name: Optional[str] = None
    list_date: Optional[int] = None
    delist_date: Optional[int] = None
    list_plate: Optional[str] = None
    is_listed: Optional[int] = None


class StockBasicSummary(BaseModel):
    """Summary of stock basic info."""

    total_count: int
    listed_count: int
    delisted_count: int
    markets: List[str] = []


class StockBasicResponse(DataResponse):
    """Response for stock basic info."""

    summary: Optional[StockBasicSummary] = None
    data: List[StockBasicItem] = []


class LimitUpItem(BaseModel):
    """Single limit-up stock record."""

    code: str
    name: str
    limitUpDate: str
    changePct: float
    board: str = "主板"
    limitUpDays: int = 1
    price: float = 0
    preClose: float = 0
    open: float = 0
    high: float = 0
    low: float = 0
    amount: float = 0
    volume: int = 0
    amplitude: float = 0
    turnover: float = 0
    firstTime: str = ""
    finalTime: str = ""
    reason: str = ""
    industry: str = ""
    concept: str = ""


class LimitUpLadderItem(BaseModel):
    """Single stock in ladder level."""

    code: str
    name: str
    level: int
    industry: str = ""
    firstTime: str = ""
    finalTime: str = ""
    reason: str = ""
    price: float = 0
    changePct: float = 0
    limitUpDate: str = ""


class LimitUpLadderLevel(BaseModel):
    """A level in the limit-up ladder."""

    level: int
    name: str
    count: int
    stocks: List[LimitUpLadderItem] = []


class LimitUpResponse(DataResponse):
    """Response for limit-up stocks."""

    date: str
    stocks: List[LimitUpItem] = []


class LimitUpLadderResponse(BaseResponse):
    """Response for limit-up ladder."""

    date: str
    total: int = 0
    maxLevel: int = 0
    levels: List[LimitUpLadderLevel] = []


# ============================================================
# Limit-Down Data
# ============================================================


class LimitDownItem(BaseModel):
    """Single limit-down stock record."""

    code: str
    name: str
    limitDownDate: str
    changePct: float
    board: str = "主板"
    limitDownDays: int = 1
    price: float = 0
    preClose: float = 0
    open: float = 0
    high: float = 0
    low: float = 0
    amount: float = 0
    volume: int = 0
    amplitude: float = 0
    turnover: float = 0
    firstTime: str = ""
    finalTime: str = ""
    reason: str = ""
    industry: str = ""
    concept: str = ""


class LimitDownResponse(DataResponse):
    """Response for limit-down stocks."""

    date: str
    stocks: List[LimitDownItem] = []


# ============================================================
# Market Activity (赚钱效应)
# ============================================================


class MarketActivityDistribution(BaseModel):
    """Market-wide rise/fall distribution."""

    rising: int = 0
    limit_up: int = 0
    real_limit_up: int = 0
    falling: int = 0
    limit_down: int = 0
    real_limit_down: int = 0
    flat: int = 0
    suspended: int = 0
    total: int = 0


class MarketActivityResponse(DataResponse):
    """Response for market activity / 赚钱效应."""

    date: str
    distribution: MarketActivityDistribution
    activity_rate: float = 0


# ============================================================
# Strong Stock Pool (强势股池)
# ============================================================


class StrongStockItem(BaseModel):
    """Single strong stock record."""

    code: str
    name: str
    changePct: float
    price: float
    amount: float
    volume: int
    turnover: float = 0
    is_new_high: bool = False
    limit_up_count: int = 0
    volume_ratio: float = 0
    industry: str = ""
    reason: str = ""


class StrongStockPoolResponse(DataResponse):
    """Response for strong stock pool."""

    date: str
    stocks: List[StrongStockItem] = []


# ============================================================
# Financial Data
# ============================================================


class FinancialRequest(BaseModel):
    """Request for financial data."""

    codes: str = Field(description="Comma-separated stock codes")
    statement_type: Literal["balance", "income", "cashflow", "profit_express", "profit_notice"] = Field(
        default="balance", description="Statement type"
    )
    begin_date: Optional[int] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[int] = Field(default=None, description="End date YYYYMMDD")


class FinancialResponse(DataResponse):
    """Response for financial data."""

    statement_type: str
    data: List[Dict[str, Any]] = []


# ============================================================
# Technical Analysis
# ============================================================


class TechnicalRequest(BaseModel):
    """Request for technical analysis."""

    code: str = Field(description="Stock code, e.g. '000001.SZ'")
    begin_date: Optional[int] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[int] = Field(default=None, description="End date YYYYMMDD")
    indicator: Optional[str] = Field(default=None, description="Specific indicator name")
    category: Optional[str] = Field(
        default=None,
        description="Category: overbought_oversold, trend, energy, volume, ma, path, other",
    )


class TechnicalIndicatorValue(BaseModel):
    """Single indicator value."""

    name: str
    values: Dict[str, Optional[float]]


class TechnicalCategory(BaseModel):
    """Category of technical indicators."""

    name: str
    indicators: List[TechnicalIndicatorValue]


class TechnicalResponse(DataResponse):
    """Response for technical analysis."""

    code: str
    date: str
    price: Dict[str, float]
    categories: Dict[str, TechnicalCategory] = {}


# ============================================================
# Fundamental Analysis
# ============================================================


class FundamentalRequest(BaseModel):
    """Request for fundamental analysis."""

    code: str = Field(description="Stock code")
    category: Optional[str] = Field(
        default=None,
        description="Category: profitability, growth, efficiency, earnings_quality, safety, governance, valuation, shareholder, size",
    )
    factor: Optional[str] = Field(default=None, description="Specific factor name")
    begin_date: Optional[int] = Field(default=None, description="K-line start date for daily metrics")
    end_date: Optional[int] = Field(default=None, description="K-line end date for daily metrics")


class FundamentalCategory(BaseModel):
    """Category of fundamental indicators."""

    name: str
    freq: str
    latest_period: Optional[str] = None
    latest_date: Optional[str] = None
    latest_values: Dict[str, Optional[float]] = {}
    history: List[Dict[str, Any]] = []


class FundamentalResponse(DataResponse):
    """Response for fundamental analysis."""

    code: str
    analysis_date: str
    categories: Dict[str, FundamentalCategory] = {}


# ============================================================
# Factor Analysis
# ============================================================


class FactorAnalysisRequest(BaseModel):
    """Request for single factor analysis."""

    factor_name: str = Field(description="Factor name")
    stock_list: List[str] = Field(description="List of stock codes")
    begin_date: int = Field(description="Start date YYYYMMDD")
    end_date: int = Field(description="End date YYYYMMDD")
    methods: List[str] = Field(default=["ic", "regression", "stratification"], description="Analysis methods")
    output_format: Literal["json", "html"] = Field(default="json")


class FactorCompositeRequest(BaseModel):
    """Request for multi-factor composite."""

    factors: Dict[str, Dict[str, Any]] = Field(description="Factor definitions")
    stock_list: List[str] = Field(description="List of stock codes")
    begin_date: int = Field(description="Start date YYYYMMDD")
    end_date: int = Field(description="End date YYYYMMDD")
    weight_method: str = Field(default="ic_ir", description="Weighting method")
    use_orthogonal: bool = Field(default=True, description="Whether to orthogonalize")
    output_format: Literal["json", "html"] = Field(default="json")


class FactorAnalysisResponse(DataResponse):
    """Response for factor analysis."""

    factor_name: str
    report_url: Optional[str] = None
    ic_analysis: Optional[Dict[str, Any]] = None
    regression: Optional[Dict[str, Any]] = None
    stratification: Optional[Dict[str, Any]] = None
    crowding: Optional[Dict[str, Any]] = None


# ============================================================
# Health & Metrics
# ============================================================


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str
    timestamp: datetime
    datasource_connected: bool
    redis_connected: bool
    auth_enabled: bool = False
    rate_limit_enabled: bool = False
    metrics_enabled: bool = False


class LoginStatusResponse(BaseModel):
    """Data-source login status."""

    is_logged_in: bool
    login_info: Optional[Dict[str, Any]] = None
    uptime_seconds: Optional[float] = None


# ============================================================
# Historical (L3) Warehouse
# ============================================================


class HistoricalKlineRequest(BaseModel):
    """Request payload for the L3 K-line endpoint."""

    codes: str = Field(description="Comma-separated stock codes")
    begin_date: int = Field(description="Start date YYYYMMDD")
    end_date: int = Field(description="End date YYYYMMDD")
    period: str = Field(default="day", description="Period: day, week, month")
    limit: Optional[int] = Field(default=None, description="Max records")
    offset: int = Field(default=0, description="Records to skip")
    source: str = Field(
        default="auto",
        description="Data source: auto, warehouse, sdk",
    )

    @field_validator("codes")
    @classmethod
    def validate_codes(cls, v: str) -> str:
        if not v:
            raise ValueError("codes cannot be empty")
        return v


class HistoricalCalendarRequest(BaseModel):
    """Request payload for the L3 calendar endpoint."""

    market: str = Field(default="SH", description="Market code")
    begin_date: Optional[int] = Field(default=None, description="Start date YYYYMMDD")
    end_date: Optional[int] = Field(default=None, description="End date YYYYMMDD")


class HistoricalCodesRequest(BaseModel):
    """Request payload for the L3 codes endpoint."""

    board: Optional[str] = Field(default=None, description="Filter by board")
    is_listed: Optional[bool] = Field(default=None, description="Filter by listing status")


class HistoricalSqlRequest(BaseModel):
    """Request payload for the constrained SQL endpoint."""

    sql: str = Field(description="SQL SELECT statement")
    timeout: Optional[int] = Field(default=None, description="Override timeout seconds")
    max_rows: Optional[int] = Field(default=None, description="Override result limit")


class HistoricalKlineRecord(BaseModel):
    """A single K-line record as returned by the L3 endpoint."""

    code: str
    date: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    adj_factor: Optional[float] = None
    is_suspended: Optional[bool] = None
    sync_at: Optional[int] = None


class HistoricalKlineResponse(DataResponse):
    """Response payload for the L3 K-line endpoint."""

    codes: List[str] = []
    period: str = "day"
    begin_date: int
    end_date: int
    source: str = "warehouse"
    synced: bool = True
    data: List[HistoricalKlineRecord] = []


class HistoricalCalendarResponse(DataResponse):
    """Response payload for the L3 calendar endpoint."""

    market: str
    data: List[Dict[str, Any]] = []


class HistoricalCodesResponse(DataResponse):
    """Response payload for the L3 codes endpoint."""

    data: List[Dict[str, Any]] = []


class HistoricalSqlResponse(DataResponse):
    """Response payload for the constrained SQL endpoint."""

    columns: List[str] = []
    rows: List[List[Any]] = []
    row_count: int = 0
    truncated: bool = False


# ============================================================
# Real-time Data
# ============================================================


class RealtimeQuotesResponse(DataResponse):
    """Response for real-time snapshot quotes."""

    data: List[Dict[str, Any]] = []


class RealtimeStatsResponse(BaseResponse):
    """Realtime subscriber and WebSocket statistics."""

    ws_connections: int = 0
    ws_subscribed_codes: int = 0
    ws_total_subscriptions: int = 0
    total_received: int = 0
    saved_to_redis: int = 0
    ws_broadcasts: int = 0
    failed: int = 0
    start_time: Optional[str] = None
