"""Real-time broadcast service for API server.

Listens to Redis Pub/Sub channels and pushes messages to WebSocket/SSE clients.
Runs in the API service process; has no dependency on any data-source SDK.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from adshare.core.cache import get_cache_manager
from adshare.core.config import get_settings
from adshare.core.logging import get_logger
from adshare.core.realtime_keys import (
    CHANNEL_INDEX,
    CHANNEL_KLINE_PREFIX,
    CHANNEL_QUOTE,
)

logger = get_logger(__name__)


class WSConnectionManager:
    """Manage WebSocket connections and per-client code subscriptions."""

    def __init__(self) -> None:
        self._connections: Dict[str, WebSocket] = {}
        self._subscriptions: Dict[str, set] = {}
        self._code_subscribers: Dict[str, set] = {}
        self._lock = threading.Lock()

    def connect(self, websocket: WebSocket) -> str:
        """Accept a WebSocket connection and return a client_id."""
        client_id = f"ws_{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._connections[client_id] = websocket
            self._subscriptions[client_id] = set()
        logger.info("WebSocket connected: %s", client_id)
        return client_id

    def disconnect(self, client_id: str) -> None:
        """Remove a client and clean up subscriptions."""
        with self._lock:
            codes = self._subscriptions.get(client_id, set())
            for code in codes:
                if code in self._code_subscribers:
                    self._code_subscribers[code].discard(client_id)
                    if not self._code_subscribers[code]:
                        del self._code_subscribers[code]
            self._connections.pop(client_id, None)
            self._subscriptions.pop(client_id, None)
        logger.info("WebSocket disconnected: %s", client_id)

    def subscribe(self, client_id: str, codes: List[str]) -> None:
        """Subscribe a client to a set of stock codes."""
        with self._lock:
            if client_id not in self._subscriptions:
                return
            old_codes = self._subscriptions.get(client_id, set())
            for code in old_codes:
                if code in self._code_subscribers:
                    self._code_subscribers[code].discard(client_id)
                    if not self._code_subscribers[code]:
                        del self._code_subscribers[code]

            new_codes = set(codes)
            self._subscriptions[client_id] = new_codes
            for code in new_codes:
                if code not in self._code_subscribers:
                    self._code_subscribers[code] = set()
                self._code_subscribers[code].add(client_id)
        logger.info("Client %s subscribed to %s codes", client_id, len(codes))

    def get_subscribers_for_code(self, code: str) -> List[str]:
        """Return all client_ids subscribed to a given code."""
        with self._lock:
            return list(self._code_subscribers.get(code, set()))

    def get_websocket(self, client_id: str) -> Optional[WebSocket]:
        """Get the WebSocket instance for a client_id."""
        return self._connections.get(client_id)

    def get_stats(self) -> Dict[str, Any]:
        """Return connection statistics."""
        with self._lock:
            return {
                "active_connections": len(self._connections),
                "subscribed_codes": len(self._code_subscribers),
                "total_subscriptions": sum(len(s) for s in self._subscriptions.values()),
            }


class SSEClientQueue(asyncio.Queue):
    """Asyncio Queue with subscribed codes tracking for SSE clients."""

    def __init__(self, maxsize: int = 1000) -> None:
        super().__init__(maxsize=maxsize)
        self.subscribed_codes: set = set()
        self.client_id: str = ""


class RealtimeBroadcastService:
    """API-side real-time broadcast service.

    - Manages WebSocket connections (via :class:`WSConnectionManager`)
    - Listens to Redis Pub/Sub and pushes to WebSocket/SSE clients
    - Runs in the API service process
    """

    def __init__(self) -> None:
        self.ws_manager = WSConnectionManager()
        self._sse_queues: Dict[str, SSEClientQueue] = {}
        self._pubsub: Optional[Any] = None
        self._listen_task: Optional[asyncio.Task] = None

        self.stats: Dict[str, Any] = {
            "ws_connections": 0,
            "ws_broadcasts": 0,
            "sse_connections": 0,
            "sse_broadcasts": 0,
            "redis_messages": 0,
            "start_time": None,
        }

    # ============================================================
    # Lifecycle
    # ============================================================

    async def start(self) -> None:
        """Start Redis Pub/Sub listener."""
        redis_client = get_cache_manager().redis
        self._pubsub = redis_client.pubsub()

        channels: List[str] = [CHANNEL_QUOTE, CHANNEL_INDEX]
        for period in get_settings().realtime_kline_periods:
            channels.append(f"{CHANNEL_KLINE_PREFIX}{period}")

        self._pubsub.subscribe(*channels)
        self._listen_task = asyncio.create_task(self._listen_loop())
        self.stats["start_time"] = datetime.now().isoformat()
        logger.info(
            "RealtimeBroadcastService started, subscribed to %d channels",
            len(channels),
        )

    async def stop(self) -> None:
        """Stop listener."""
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        if self._pubsub:
            self._pubsub.unsubscribe()
            self._pubsub.close()

        logger.info("RealtimeBroadcastService stopped")

    # ============================================================
    # Redis Pub/Sub Listener
    # ============================================================

    async def _listen_loop(self) -> None:
        """Redis Pub/Sub listen loop.

        Uses :func:`asyncio.to_thread` because ``pubsub.get_message`` is a
        blocking call.
        """
        logger.info("Pub/Sub listen loop started")
        while True:
            try:
                message = await asyncio.to_thread(
                    self._pubsub.get_message, timeout=1.0
                )

                if message is None or message["type"] != "message":
                    continue

                self.stats["redis_messages"] += 1

                try:
                    payload = json.loads(message["data"])
                    code = payload.get("code", "")
                    if not code:
                        continue

                    # Broadcast to WebSocket clients
                    await self._broadcast_ws(code, payload)

                    # Broadcast to SSE clients
                    await self._broadcast_sse(code, payload)

                except json.JSONDecodeError as e:
                    logger.error("Invalid JSON in Pub/Sub message: %s", e)
                except Exception as e:
                    logger.error("Broadcast error: %s", e)

            except asyncio.CancelledError:
                logger.info("Pub/Sub listen loop cancelled")
                break
            except Exception as e:
                logger.error("Listen loop error: %s", e)
                await asyncio.sleep(1)

    # ============================================================
    # WebSocket Broadcast
    # ============================================================

    async def _broadcast_ws(self, code: str, payload: Dict[str, Any]) -> None:
        """Push message to WebSocket clients subscribed to *code*."""
        subscribers = self.ws_manager.get_subscribers_for_code(code)
        if not subscribers:
            return

        disconnected: List[str] = []

        for client_id in subscribers:
            ws = self.ws_manager.get_websocket(client_id)
            if ws is None:
                disconnected.append(client_id)
                continue

            try:
                await ws.send_json(payload)
                self.stats["ws_broadcasts"] += 1
            except Exception:
                disconnected.append(client_id)

        for cid in disconnected:
            self.ws_manager.disconnect(cid)

        self.stats["ws_connections"] = self.ws_manager.get_stats()[
            "active_connections"
        ]

    # ============================================================
    # SSE Broadcast
    # ============================================================

    async def _broadcast_sse(self, code: str, payload: Dict[str, Any]) -> None:
        """Push message to SSE clients subscribed to *code*."""
        for client_id, queue in list(self._sse_queues.items()):
            if code in queue.subscribed_codes:
                try:
                    queue.put_nowait(payload)
                    self.stats["sse_broadcasts"] += 1
                except asyncio.QueueFull:
                    logger.warning("SSE queue full for client %s", client_id)

    def register_sse_client(self, codes: set) -> SSEClientQueue:
        """Register an SSE client and return its message queue."""
        client_id = f"sse_{uuid.uuid4().hex[:8]}"
        queue = SSEClientQueue(maxsize=1000)
        queue.subscribed_codes = codes
        queue.client_id = client_id
        self._sse_queues[client_id] = queue
        self.stats["sse_connections"] = len(self._sse_queues)
        logger.info("SSE client registered: %s, codes=%s", client_id, codes)
        return queue

    def unregister_sse_client(self, client_id: str) -> None:
        """Unregister an SSE client."""
        self._sse_queues.pop(client_id, None)
        self.stats["sse_connections"] = len(self._sse_queues)
        logger.info("SSE client unregistered: %s", client_id)

    def get_stats(self) -> Dict[str, Any]:
        """Return service statistics."""
        ws_stats = self.ws_manager.get_stats()
        return {
            **self.stats,
            "ws_active_connections": ws_stats["active_connections"],
            "ws_subscribed_codes": ws_stats["subscribed_codes"],
            "ws_total_subscriptions": ws_stats["total_subscriptions"],
        }


# Singleton
_broadcast_service: Optional[RealtimeBroadcastService] = None


def get_broadcast_service() -> RealtimeBroadcastService:
    """Return the global :class:`RealtimeBroadcastService` singleton."""
    global _broadcast_service
    if _broadcast_service is None:
        _broadcast_service = RealtimeBroadcastService()
    return _broadcast_service
