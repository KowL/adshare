"""Market data routers."""

from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from adshare.adapters.amazingdata import get_adapter
from adshare.core.cache import get_cache_manager
from adshare.core.logging import get_logger
from adshare.models.schemas import (
    CalendarResponse,
    CodeListResponse,
    KlineResponse,
    LimitUpItem,
    LimitUpLadderItem,
    LimitUpLadderLevel,
    LimitUpLadderResponse,
    LimitUpResponse,
    SnapshotResponse,
    StockBasicResponse,
    StockBasicSummary,
)
from adshare.services.mappers import dataframe_to_kline_items, dataframe_to_snapshot_items
from adshare.services.market_data import get_market_data_service

logger = get_logger(__name__)
router = APIRouter(prefix="/market", tags=["market"])


@router.get("/codes", response_model=CodeListResponse)
async def get_code_list(
    security_type: str = Query(default="EXTRA_STOCK_A", description="Security type")
):
    """Get security code list."""
    try:
        service = get_market_data_service()
        codes = service.get_code_list(security_type=security_type)
        return CodeListResponse(
            security_type=security_type,
            code_list=codes,
            count=len(codes),
            data=codes,
        )
    except Exception as e:
        logger.error(f"get_code_list failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/calendar", response_model=CalendarResponse)
async def get_calendar(
    market: str = Query(default="SH", description="Market code"),
    date: Optional[int] = Query(default=None, description="Query date YYYYMMDD"),
):
    """Get trading calendar."""
    try:
        service = get_market_data_service()
        df = service.get_calendar(market=market, date=date)
        calendar = df["date"].tolist() if "date" in df.columns else []
        return CalendarResponse(
            market=market,
            query_date=date or 0,
            calendar=calendar,
            count=len(calendar),
            data=calendar,
        )
    except Exception as e:
        logger.error(f"get_calendar failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/kline", response_model=KlineResponse)
async def get_kline(
    codes: str = Query(..., description="Comma-separated stock codes"),
    begin_date: int = Query(..., description="Start date YYYYMMDD"),
    end_date: int = Query(..., description="End date YYYYMMDD"),
    period: str = Query(default="day", description="Period: day, week, month, min1, min5"),
    limit: Optional[int] = Query(default=None, description="Max records"),
    offset: int = Query(default=0, description="Records to skip"),
):
    """Get K-line data.

    Lookup order: L1 Redis -> L3 historical warehouse -> L2 temp cache -> SDK.
    """
    try:
        service = get_market_data_service()
        result = service.get_kline(
            codes=codes,
            begin_date=begin_date,
            end_date=end_date,
            period=period,
            limit=limit,
            offset=offset,
            source="auto",
        )
        df = result.df

        items = dataframe_to_kline_items(df)

        return KlineResponse(
            codes=codes.split(","),
            period=period,
            begin_date=begin_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
            count=len(items),
            data=items,
        )
    except Exception as e:
        logger.error(f"get_kline failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/kline/simple", response_model=KlineResponse)
async def get_kline_simple(
    symbol: str = Query(..., description="Stock code, e.g. 002654.SZ"),
    count: int = Query(default=60, description="Number of bars to fetch"),
    period: str = Query(default="day", description="Period: day, week, month"),
):
    """Simplified K-line endpoint: auto-calculate date range from count.
    
    Example: /market/kline/simple?symbol=002654.SZ&count=60&period=day
    """
    from datetime import datetime, timedelta
    
    try:
        # Calculate date range
        end_dt = datetime.now()
        # Rough estimate: count days + weekends + holidays buffer
        start_dt = end_dt - timedelta(days=int(count * 1.5) + 30)
        
        begin_date = int(start_dt.strftime("%Y%m%d"))
        end_date = int(end_dt.strftime("%Y%m%d"))
        
        service = get_market_data_service()
        result = service.get_kline(
            codes=symbol,
            begin_date=begin_date,
            end_date=end_date,
            period=period,
            limit=count,
            offset=0,
            source="auto",
        )
        df = result.df

        items = dataframe_to_kline_items(df)

        return KlineResponse(
            codes=[symbol],
            period=period,
            begin_date=begin_date,
            end_date=end_date,
            limit=count,
            offset=0,
            count=len(items),
            data=items,
        )
    except Exception as e:
        logger.error(f"get_kline_simple failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/snapshot", response_model=SnapshotResponse)
async def get_snapshot(
    codes: str = Query(..., description="Comma-separated stock codes"),
    date: Optional[int] = Query(default=None, description="Trade date YYYYMMDD"),
    time: Optional[int] = Query(default=None, description="Trade time HHMMSS"),
):
    """Get snapshot data."""
    try:
        service = get_market_data_service()
        df = service.get_snapshot(codes=codes, date=date, time=time)
        items = dataframe_to_snapshot_items(df)

        return SnapshotResponse(count=len(items), data=items)
    except Exception as e:
        logger.error(f"get_snapshot failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stock/basic", response_model=StockBasicResponse)
async def get_stock_basic(
    codes: Optional[str] = Query(default=None, description="Comma-separated codes, empty for all"),
    summary_only: bool = Query(default=False, description="Return summary only"),
):
    """Get stock basic information."""
    try:
        service = get_market_data_service()
        df = service.get_stock_basic(codes=codes, summary_only=summary_only)

        if summary_only:
            summary = StockBasicSummary(
                total_count=int(df.get("total_count", 0)),
                listed_count=int(df.get("listed_count", 0)),
                delisted_count=int(df.get("delisted_count", 0)),
                markets=df.get("markets", []),
            )
            return StockBasicResponse(summary=summary, count=0, data=[])

        items = []
        for _, row in df.iterrows():
            ld = row.get("list_date")
            dd = row.get("delist_date")
            il = row.get("is_listed")
            items.append(
                {
                    "code": str(row.get("code", "")),
                    "name": str(row.get("name", "")),
                    "comp_name": str(row.get("comp_name", "")),
                    "list_date": int(ld) if pd.notna(ld) and str(ld).isdigit() else None,
                    "delist_date": int(dd) if pd.notna(dd) and str(dd).isdigit() else None,
                    "list_plate": str(row.get("list_plate", "")),
                    "is_listed": int(il) if pd.notna(il) else None,
                }
            )

        return StockBasicResponse(count=len(items), data=items)
    except Exception as e:
        logger.error(f"get_stock_basic failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Limit-Up Data
# ============================================================


def _detect_board(code: str) -> str:
    """Detect stock board from code."""
    clean = code.split(".")[0] if "." in code else code
    if clean.startswith("68"):
        return "科创板"
    if clean.startswith("8") or clean.startswith("4"):
        return "北交所"
    if clean.startswith("30"):
        return "创业板"
    if clean.startswith("60") or clean.startswith("00"):
        return "主板"
    return "主板"


def _is_limit_up(change_pct: float, board: str) -> bool:
    """Check if a stock hit limit-up today."""
    # 主板/中小板: 10%, 创业板/科创板: 20%, ST: 5%
    threshold = 0.20 if board in ("创业板", "科创板") else 0.10
    return change_pct >= threshold - 0.001  # Allow small tolerance


def _code_aliases(code: str) -> set[str]:
    """Return equivalent code keys used by different AmazingData APIs."""
    clean = str(code).strip()
    if not clean:
        return set()
    aliases = {clean}
    if "." in clean:
        aliases.add(clean.split(".", 1)[0])
    return aliases


def _build_name_map(df_info: pd.DataFrame) -> dict[str, str]:
    """Build a stock name map from common AmazingData code-info layouts."""
    if not isinstance(df_info, pd.DataFrame) or df_info.empty:
        return {}

    code_columns = ("code", "MARKET_CODE", "market_code", "security_code", "SECURITY_CODE")
    name_columns = ("name", "symbol", "SECURITY_NAME", "security_name", "SHORT_NAME", "short_name")

    code_col = next((col for col in code_columns if col in df_info.columns), None)
    name_col = next((col for col in name_columns if col in df_info.columns), None)
    if name_col is None:
        return {}

    name_map: dict[str, str] = {}
    if code_col is not None:
        rows = zip(df_info[code_col], df_info[name_col])
    else:
        rows = zip(df_info.index, df_info[name_col])

    for raw_code, raw_name in rows:
        if pd.isna(raw_code) or pd.isna(raw_name):
            continue
        name = str(raw_name).strip()
        if not name:
            continue
        for alias in _code_aliases(str(raw_code)):
            name_map[alias] = name
    return name_map


@router.get("/limit-up", response_model=LimitUpResponse)
async def get_limit_up(
    days: int = Query(default=1, description="Number of trading days to look back"),
    board_filter: Optional[str] = Query(default=None, description="Filter by board: 主板, 创业板, 科创板, 北交所"),
    exclude_st: bool = Query(default=True, description="Exclude ST/*ST stocks"),
):
    """Get limit-up stocks for recent trading days.
    
    This endpoint calculates limit-up stocks from snapshot data.
    Since AmazingData SDK doesn't provide native limit-up data,
    we compute it by checking if a stock's daily change >= limit threshold.
    """
    cache = get_cache_manager()
    today_str = _today_str()
    cache_key = ("limit_up", today_str, str(days), str(board_filter or "all"), str(exclude_st))
    cached = cache.get("snapshot", *cache_key)
    if cached is not None:
        return LimitUpResponse(date=today_str, stocks=cached, count=len(cached), data=cached)

    try:
        adapter = get_adapter()

        # Check if AmazingData is logged in
        if not adapter.is_logged_in:
            logger.warning("AmazingData not logged in, returning empty limit-up data")
            return LimitUpResponse(date=today_str, stocks=[], count=0)

        # Get all stock codes
        codes = adapter.get_code_list(security_type="EXTRA_STOCK_A")
        if not codes:
            return LimitUpResponse(date=today_str, stocks=[], count=0)

        # Get stock names from code_info (single call, cached)
        df_info = adapter.get_code_info(security_type="EXTRA_STOCK_A")
        name_map = _build_name_map(df_info)

        # Get today's snapshot for all stocks
        # We query in batches to avoid overwhelming the SDK
        batch_size = 200
        all_snapshots = []
        from datetime import datetime
        today = int(datetime.now().strftime("%Y%m%d"))

        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]
            try:
                df_snap = adapter.get_snapshot(codes=",".join(batch), date=today)
                if hasattr(df_snap, "iterrows"):
                    all_snapshots.extend(df_snap.to_dict("records"))
            except Exception as e:
                logger.warning(f"Snapshot batch {i}-{i+batch_size} failed: {e}")
                continue

        # Calculate limit-up stocks
        limit_up_stocks = []
        for row in all_snapshots:
            code = str(row.get("code", ""))
            if not code:
                continue

            board = _detect_board(code)

            # Board filter
            if board_filter and board != board_filter:
                continue

            # Get price data
            close = float(row.get("close", 0) or 0)
            pre_close = float(row.get("pre_close", row.get("preClose", 0)) or 0)
            open_price = float(row.get("open", 0) or 0)
            high = float(row.get("high", 0) or 0)
            low = float(row.get("low", 0) or 0)
            volume = int(row.get("volume", 0) or 0)
            amount = float(row.get("amount", 0) or 0)

            if pre_close <= 0:
                continue

            change_pct = (close - pre_close) / pre_close

            # Check limit-up
            if not _is_limit_up(change_pct, board):
                continue

            # Exclude ST
            name = name_map.get(code) or name_map.get(code.split(".")[0]) or code
            if exclude_st and ("ST" in name or "*ST" in name or name.startswith("ST") or name.startswith("*ST")):
                continue

            limit_up_stocks.append(
                LimitUpItem(
                    code=code.split(".")[0] if "." in code else code,
                    name=name,
                    limitUpDate=today_str,
                    changePct=round(change_pct, 4),
                    board=board,
                    limitUpDays=1,  # We only know today's status from snapshot
                    price=round(close, 2),
                    preClose=round(pre_close, 2),
                    open=round(open_price, 2),
                    high=round(high, 2),
                    low=round(low, 2),
                    amount=round(amount, 2),
                    volume=volume,
                    amplitude=round((high - low) / pre_close, 4) if pre_close > 0 else 0,
                    turnover=0,  # Not available in snapshot
                    firstTime="",
                    finalTime="",
                    reason="",
                    industry="",
                    concept="",
                )
            )

        # Cache the result (TTL handled by cache manager for snapshot type)
        cache.set("snapshot", limit_up_stocks, *cache_key)

        return LimitUpResponse(
            date=today_str,
            stocks=limit_up_stocks,
            count=len(limit_up_stocks),
            data=limit_up_stocks,
        )
    except Exception as e:
        logger.error(f"get_limit_up failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/limit-up/ladder", response_model=LimitUpLadderResponse)
async def get_limit_up_ladder(
    days: int = Query(default=15, description="Number of trading days to look back"),
    board_filter: Optional[str] = Query(default=None, description="Filter by board"),
    exclude_st: bool = Query(default=True, description="Exclude ST/*ST stocks"),
):
    """Get limit-up ladder (consecutive limit-up levels).
    
    Note: Since we only have today's snapshot data, limitUpDays is always 1.
    For true consecutive days calculation, historical K-line data would be needed.
    """
    try:
        # Get today's limit-up stocks
        limit_up_resp = await get_limit_up(days=1, board_filter=board_filter, exclude_st=exclude_st)
        stocks = limit_up_resp.stocks
        
        if not stocks:
            return LimitUpLadderResponse(
                date=_today_str(),
                total=0,
                maxLevel=0,
                levels=[],
            )
        
        # Group by limit-up days (all are 1 day from snapshot)
        # For now, all are "首板" since we only have 1 day of data
        level_map = {1: []}
        for s in stocks:
            level_map[1].append(s)
        
        levels = []
        for lv in sorted(level_map.keys(), reverse=True):
            stocks_in_level = level_map[lv]
            # Sort by changePct descending
            stocks_in_level.sort(key=lambda x: x.changePct, reverse=True)
            
            ladder_stocks = [
                LimitUpLadderItem(
                    code=s.code,
                    name=s.name,
                    level=lv,
                    industry=s.industry,
                    firstTime=s.firstTime,
                    finalTime=s.finalTime,
                    reason=s.reason,
                    price=s.price,
                    changePct=s.changePct,
                    limitUpDate=s.limitUpDate,
                )
                for s in stocks_in_level
            ]
            
            levels.append(
                LimitUpLadderLevel(
                    level=lv,
                    name="首板" if lv == 1 else f"{lv}连板",
                    count=len(ladder_stocks),
                    stocks=ladder_stocks,
                )
            )
        
        return LimitUpLadderResponse(
            date=_today_str(),
            total=len(stocks),
            maxLevel=1,
            levels=levels,
        )
    except Exception as e:
        logger.error(f"get_limit_up_ladder failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _today_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")
