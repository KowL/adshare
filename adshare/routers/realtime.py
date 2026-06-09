"""Real-time market data routers (WebSocket + REST).

Provides tick-level snapshot quotes pushed via WebSocket and cached in Redis.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from adshare.core.cache import get_cache_manager
from adshare.core.logging import get_logger
from adshare.models.schemas import RealtimeQuotesResponse, RealtimeStatsResponse
from adshare.services.realtime import get_realtime_subscriber

logger = get_logger(__name__)
router = APIRouter(prefix="/realtime", tags=["realtime"])

REALTIME_QUOTE_KEY = "realtime:quote"


# ============================================================
# REST API
# ============================================================


@router.get("/quote/{code}", response_model=RealtimeQuotesResponse)
async def get_realtime_quote(
    code: str,
):
    """Get the latest real-time quote for a single stock from Redis cache."""
    try:
        cache = get_cache_manager()
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
):
    """Get latest real-time quotes for multiple stocks from Redis cache."""
    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        cache = get_cache_manager()
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


@router.get("/stats", response_model=RealtimeStatsResponse)
async def get_realtime_stats():
    """Get realtime subscriber statistics and WebSocket connection info."""
    try:
        subscriber = get_realtime_subscriber()
        ws_stats = subscriber.ws_manager.get_stats()
        return RealtimeStatsResponse(
            ws_connections=ws_stats["active_connections"],
            ws_subscribed_codes=ws_stats["subscribed_codes"],
            ws_total_subscriptions=ws_stats["total_subscriptions"],
            total_received=subscriber.stats["total_received"],
            saved_to_redis=subscriber.stats["saved_to_redis"],
            ws_broadcasts=subscriber.stats["ws_broadcasts"],
            failed=subscriber.stats["failed"],
            start_time=subscriber.stats["start_time"],
        )
    except Exception as e:
        logger.error("get_realtime_stats failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# WebSocket
# ============================================================


@router.websocket("/ws")
async def realtime_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time quote streaming.

    Protocol:
      - Connect → server sends {"type": "connected", "client_id": "..."}
      - Subscribe → send {"action": "subscribe", "codes": ["000001.SZ", ...]}
      - Unsubscribe → send {"action": "unsubscribe"} (clears all)
      - Ping → send {"action": "ping"} → server replies {"type": "pong"}
      - Quote data → server pushes {"type": "quote", "code": "...", "data": {...}}
    """
    subscriber = get_realtime_subscriber()
    await websocket.accept()
    client_id = subscriber.ws_manager.connect(websocket)

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
                subscriber.ws_manager.subscribe(client_id, codes)
                await websocket.send_json(
                    {"type": "subscribed", "codes": codes, "count": len(codes)}
                )

            elif action == "unsubscribe":
                subscriber.ws_manager.subscribe(client_id, [])
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
        subscriber.ws_manager.disconnect(client_id)
