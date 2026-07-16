"""Real-time market data routers (WebSocket + SSE + REST).

Provides tick-level snapshot quotes pushed via WebSocket/SSE and cached in Redis.
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from adshare import dependencies as deps
from adshare.core.cache import CacheManager
from adshare.core.logging import get_logger
from adshare.core.realtime_keys import (
    REALTIME_INDEX_KEY,
    REALTIME_KLINE_KEY,
    REALTIME_QUOTE_KEY,
)
from adshare.models.schemas import RealtimeQuotesResponse, RealtimeStatsResponse
from adshare.services.realtime_broadcast import RealtimeBroadcastService

logger = get_logger(__name__)
router = APIRouter(prefix="/realtime", tags=["realtime"])

# ============================================================
# REST API — Snapshot Quotes
# ============================================================


@router.get("/quote/{code}", response_model=RealtimeQuotesResponse)
async def get_realtime_quote(
    code: str,
    cache: CacheManager = Depends(deps.get_cache_manager_dep),
):
    """Get the latest real-time quote for a single stock from Redis cache."""
    try:
        data = cache.get_realtime_market(REALTIME_QUOTE_KEY, code)
        if data is None:
            return RealtimeQuotesResponse(
                count=0,
                data=[],
                message=f"No realtime data cached for {code}",
            )
        return RealtimeQuotesResponse(
            count=1,
            data=[{"code": code, **data}],
        )
    except Exception as e:
        logger.error("get_realtime_quote failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/quotes", response_model=RealtimeQuotesResponse)
async def get_realtime_quotes(
    codes: str = Query(..., description="Comma-separated stock codes"),
    cache: CacheManager = Depends(deps.get_cache_manager_dep),
):
    """Get latest real-time quotes for multiple stocks from Redis cache."""
    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        results: List[Dict[str, Any]] = []
        for code in code_list:
            data = cache.get_realtime_market(REALTIME_QUOTE_KEY, code)
            if data is not None:
                results.append({"code": code, **data})
        return RealtimeQuotesResponse(
            count=len(results),
            data=results,
        )
    except Exception as e:
        logger.error("get_realtime_quotes failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# REST API — Index Snapshot
# ============================================================


@router.get("/index/{code}", response_model=RealtimeQuotesResponse)
async def get_realtime_index(
    code: str,
    cache: CacheManager = Depends(deps.get_cache_manager_dep),
):
    """Get the latest real-time index snapshot from Redis cache."""
    try:
        data = cache.get_realtime_market(REALTIME_INDEX_KEY, code)
        if data is None:
            return RealtimeQuotesResponse(
                count=0,
                data=[],
                message=f"No realtime index data cached for {code}",
            )
        return RealtimeQuotesResponse(
            count=1,
            data=[{"code": code, **data}],
        )
    except Exception as e:
        logger.error("get_realtime_index failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/index", response_model=RealtimeQuotesResponse)
async def get_realtime_index_batch(
    codes: str = Query(..., description="Comma-separated index codes"),
    cache: CacheManager = Depends(deps.get_cache_manager_dep),
):
    """Get latest real-time index snapshots for multiple indices."""
    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        results: List[Dict[str, Any]] = []
        for code in code_list:
            data = cache.get_realtime_market(REALTIME_INDEX_KEY, code)
            if data is not None:
                results.append({"code": code, **data})
        return RealtimeQuotesResponse(
            count=len(results),
            data=results,
        )
    except Exception as e:
        logger.error("get_realtime_index_batch failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# REST API — Realtime K-line
# ============================================================


@router.get("/kline/{code}", response_model=RealtimeQuotesResponse)
async def get_realtime_kline(
    code: str,
    period: str = Query(default="min1", description="K-line period: min1, min5, min15, min30, min60, day, week, month"),
    cache: CacheManager = Depends(deps.get_cache_manager_dep),
):
    """Get the latest real-time K-line tick for a single stock from Redis cache."""
    try:
        data = cache.get_realtime_market(REALTIME_KLINE_KEY, period, code)
        if data is None:
            return RealtimeQuotesResponse(
                count=0,
                data=[],
                message=f"No realtime kline cached for {code} ({period})",
            )
        return RealtimeQuotesResponse(
            count=1,
            data=[{"code": code, "period": period, **data}],
        )
    except Exception as e:
        logger.error("get_realtime_kline failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/kline", response_model=RealtimeQuotesResponse)
async def get_realtime_kline_batch(
    codes: str = Query(..., description="Comma-separated stock codes"),
    period: str = Query(default="min1", description="K-line period"),
    cache: CacheManager = Depends(deps.get_cache_manager_dep),
):
    """Get latest real-time K-line ticks for multiple stocks."""
    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        results: List[Dict[str, Any]] = []
        for code in code_list:
            data = cache.get_realtime_market(REALTIME_KLINE_KEY, period, code)
            if data is not None:
                results.append({"code": code, "period": period, **data})
        return RealtimeQuotesResponse(
            count=len(results),
            data=results,
        )
    except Exception as e:
        logger.error("get_realtime_kline_batch failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Stats
# ============================================================


@router.get("/stats", response_model=RealtimeStatsResponse)
async def get_realtime_stats(
    broadcast: RealtimeBroadcastService = Depends(deps.get_broadcast_service_dep),
):
    """Get realtime broadcast statistics and WebSocket connection info."""
    try:
        stats = broadcast.get_stats()
        return RealtimeStatsResponse(
            ws_connections=stats["ws_active_connections"],
            ws_subscribed_codes=stats["ws_subscribed_codes"],
            ws_total_subscriptions=stats["ws_total_subscriptions"],
            total_received=0,  # Worker-side stat, not available in API process
            saved_to_redis=0,  # Worker-side stat, not available in API process
            ws_broadcasts=stats["ws_broadcasts"],
            failed=0,
            start_time=stats["start_time"],
        )
    except Exception as e:
        logger.error("get_realtime_stats failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# WebSocket
# ============================================================


@router.websocket("/ws")
async def realtime_websocket(
    websocket: WebSocket,
    broadcast: RealtimeBroadcastService = Depends(deps.get_broadcast_service_dep),
):
    """WebSocket endpoint for real-time quote streaming.

    Protocol:
      - Connect → server sends {"type": "connected", "client_id": "..."}
      - Subscribe → send {"action": "subscribe", "codes": ["000001.SZ", ...]}
      - Unsubscribe → send {"action": "unsubscribe"} (clears all)
      - Ping → send {"action": "ping"} → server replies {"type": "pong"}
      - Quote data → server pushes {"type": "quote", "code": "...", "data": {...}}
    """
    await websocket.accept()
    client_id = broadcast.ws_manager.connect(websocket)

    try:
        await websocket.send_json({"type": "connected", "client_id": client_id})

        while True:
            raw = await websocket.receive_text()
            try:
                import json

                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            action = msg.get("action", "")

            if action == "subscribe":
                codes = msg.get("codes", [])
                if not isinstance(codes, list) or not codes:
                    await websocket.send_json(
                        {"type": "error", "message": "codes must be a non-empty list"}
                    )
                    continue
                broadcast.ws_manager.subscribe(client_id, codes)
                await websocket.send_json(
                    {"type": "subscribed", "codes": codes, "count": len(codes)}
                )

            elif action == "unsubscribe":
                broadcast.ws_manager.subscribe(client_id, [])
                await websocket.send_json({"type": "unsubscribed"})

            elif action == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json(
                    {"type": "error", "message": f"Unknown action: {action}"}
                )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WebSocket error (%s): %s", client_id, e)
    finally:
        broadcast.ws_manager.disconnect(client_id)


# ============================================================
# SSE (Server-Sent Events)
# ============================================================


@router.get("/sse")
async def realtime_sse(
    request: Request,
    codes: str = Query(..., description="Comma-separated stock codes"),
    types: str = Query(default="quote", description="Data types: quote,index,kline"),
    broadcast: RealtimeBroadcastService = Depends(deps.get_broadcast_service_dep),
):
    """Server-Sent Events for real-time quotes.

    Example:
      curl -N "http://localhost:8000/realtime/sse?codes=000001.SZ,600000.SH"
    """
    from fastapi.responses import StreamingResponse

    code_set = set(c.strip() for c in codes.split(",") if c.strip())
    queue = broadcast.register_sse_client(code_set)

    async def event_generator():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield "event: heartbeat\ndata: \n\n"
        except asyncio.CancelledError:
            pass
        finally:
            broadcast.unregister_sse_client(queue.client_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
