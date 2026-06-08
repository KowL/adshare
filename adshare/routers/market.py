"""Market data routers."""

import pandas as pd
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from adshare.adapters.amazingdata import get_adapter
from adshare.core.cache import get_cache_manager
from adshare.core.logging import get_logger
from adshare.models.schemas import (
    CalendarRequest,
    CalendarResponse,
    CodeListRequest,
    CodeListResponse,
    ErrorResponse,
    KlineItem,
    KlineRequest,
    KlineResponse,
    LimitUpItem,
    LimitUpLadderItem,
    LimitUpLadderLevel,
    LimitUpLadderResponse,
    LimitUpResponse,
    SnapshotItem,
    SnapshotRequest,
    SnapshotResponse,
    StockBasicRequest,
    StockBasicResponse,
    StockBasicSummary,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/market", tags=["market"])


@router.get("/codes", response_model=CodeListResponse)
async def get_code_list(
    security_type: str = Query(default="EXTRA_STOCK_A", description="Security type")
):
    """Get security code list."""
    try:
        adapter = get_adapter()
        codes = adapter.get_code_list(security_type=security_type)
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
        adapter = get_adapter()
        df = adapter.get_calendar(market=market, date=date)
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
    """Get K-line data."""
    try:
        adapter = get_adapter()
        df = adapter.get_kline(
            codes=codes,
            begin_date=begin_date,
            end_date=end_date,
            period=period,
            limit=limit,
            offset=offset,
        )

        items = []
        for _, row in df.iterrows():
            # AmazingData returns 'kline_time' as Timestamp, convert to int YYYYMMDD
            date_val = row.get("date") or row.get("kline_time") or 0
            if hasattr(date_val, "strftime"):
                date_val = int(date_val.strftime("%Y%m%d"))
            else:
                date_val = int(date_val) if date_val else 0
            items.append(
                KlineItem(
                    code=str(row.get("code", "")),
                    date=date_val,
                    open=float(row.get("open", 0)),
                    high=float(row.get("high", 0)),
                    low=float(row.get("low", 0)),
                    close=float(row.get("close", 0)),
                    volume=int(row.get("volume", 0)),
                    amount=float(row.get("amount", 0)),
                )
            )

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
        adapter = get_adapter()
        
        # Calculate date range
        end_dt = datetime.now()
        # Rough estimate: count days + weekends + holidays buffer
        start_dt = end_dt - timedelta(days=int(count * 1.5) + 30)
        
        begin_date = int(start_dt.strftime("%Y%m%d"))
        end_date = int(end_dt.strftime("%Y%m%d"))
        
        df = adapter.get_kline(
            codes=symbol,
            begin_date=begin_date,
            end_date=end_date,
            period=period,
            limit=count,
            offset=0,
        )

        items = []
        for _, row in df.iterrows():
            # AmazingData returns 'kline_time' as Timestamp, convert to int YYYYMMDD
            date_val = row.get("date") or row.get("kline_time") or 0
            if hasattr(date_val, "strftime"):
                date_val = int(date_val.strftime("%Y%m%d"))
            else:
                date_val = int(date_val) if date_val else 0
            items.append(
                KlineItem(
                    code=str(row.get("code", symbol)),
                    date=date_val,
                    open=float(row.get("open", 0)),
                    high=float(row.get("high", 0)),
                    low=float(row.get("low", 0)),
                    close=float(row.get("close", 0)),
                    volume=int(row.get("volume", 0)),
                    amount=float(row.get("amount", 0)),
                )
            )

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
        adapter = get_adapter()
        
        # Check if AmazingData is logged in
        if not adapter.is_logged_in:
            logger.warning("AmazingData not logged in, returning empty snapshot data")
            return SnapshotResponse(count=0, data=[])
        
        df = adapter.get_snapshot(codes=codes, date=date, time=time)

        items = []
        for _, row in df.iterrows():
            items.append(
                SnapshotItem(
                    code=str(row.get("code", "")),
                    date=int(row.get("date", 0)),
                    time=int(row.get("time", 0)) if "time" in row else None,
                    open=float(row.get("open")) if "open" in row else None,
                    high=float(row.get("high")) if "high" in row else None,
                    low=float(row.get("low")) if "low" in row else None,
                    close=float(row.get("close")) if "close" in row else None,
                    volume=int(row.get("volume")) if "volume" in row else None,
                    amount=float(row.get("amount")) if "amount" in row else None,
                )
            )

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
        adapter = get_adapter()
        df = adapter.get_stock_basic(codes=codes, summary_only=summary_only)

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
    try:
        adapter = get_adapter()
        
        # Check if AmazingData is logged in
        if not adapter.is_logged_in:
            logger.warning("AmazingData not logged in, returning empty limit-up data")
            return LimitUpResponse(date=_today_str(), stocks=[], count=0)
        
        # Get all stock codes
        codes = adapter.get_code_list(security_type="EXTRA_STOCK_A")
        if not codes:
            return LimitUpResponse(date=_today_str(), stocks=[], count=0)
        
        # Get stock basic info for names
        df_basic = adapter.get_stock_basic(codes=",".join(codes[:500]))  # Batch 500 at a time
        name_map = {}
        if hasattr(df_basic, "iterrows"):
            for _, row in df_basic.iterrows():
                c = str(row.get("code", ""))
                name_map[c] = str(row.get("name", c))
        
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
            name = name_map.get(code, code)
            if exclude_st and ("ST" in name or "*ST" in name or name.startswith("ST") or name.startswith("*ST")):
                continue
            
            limit_up_stocks.append(
                LimitUpItem(
                    code=code.split(".")[0] if "." in code else code,
                    name=name,
                    limitUpDate=_today_str(),
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
        
        return LimitUpResponse(
            date=_today_str(),
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
