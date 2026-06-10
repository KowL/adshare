"""Real-time market data subscription service.

Wraps AmazingData SubscribeData to provide WebSocket push and Redis caching
for tick-level snapshot quotes.
"""

from __future__ import annotations

import asyncio
import math
import queue
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

from amazingdata_worker.adapters.amazingdata import get_adapter
from adshare.core.cache import get_cache_manager
from adshare.core.config import get_settings
from adshare.core.logging import get_logger

logger = get_logger(__name__)

REALTIME_QUOTE_KEY = "realtime:quote"
REALTIME_KLINE_KEY = "realtime:kline"
REALTIME_INDEX_KEY = "realtime:index"

# Period map: string -> AmazingData constant value
KLINE_PERIOD_MAP = {
    "min1": 10000,
    "min3": 10001,
    "min5": 10002,
    "min10": 10003,
    "min15": 10004,
    "min30": 10005,
    "min60": 10006,
    "day": 10008,
    "week": 10009,
    "month": 10010,
}


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


class RealtimeSubscriber:
    """Real-time snapshot quote subscriber using AmazingData SDK."""

    def __init__(self) -> None:
        self._subscribe_data: Optional[Any] = None
        self._base_data: Optional[Any] = None
        self._code_list: List[str] = []
        self._index_code_list: List[str] = []
        self._running = False
        self._subscribe_thread: Optional[threading.Thread] = None
        self.ws_manager = WSConnectionManager()
        self._broadcast_queue: queue.Queue = queue.Queue(maxsize=50000)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self.stats = {
            "total_received": 0,
            "saved_to_redis": 0,
            "ws_broadcasts": 0,
            "failed": 0,
            "start_time": None,
        }

    # ============================================================
    # Lifecycle
    # ============================================================

    def initialize(self) -> bool:
        """Login, fetch code list, set up callbacks and start subscriber thread."""
        try:
            adapter = get_adapter()
            if not adapter.ensure_login():
                logger.error("AmazingData not logged in, cannot start realtime subscriber")
                return False

            import AmazingData as ad

            self._base_data = ad.BaseData()
            self._code_list = self._base_data.get_code_list(security_type="EXTRA_STOCK_A")
            if not self._code_list:
                self._code_list = ["000001.SZ", "600000.SH", "600519.SH"]
            logger.info("Realtime subscriber: fetched %s A-share codes", len(self._code_list))

            # Index codes (§3.5.3.1 指数实时快照)
            try:
                self._index_code_list = self._base_data.get_code_list(security_type="EXTRA_INDEX_A")
                logger.info("Realtime subscriber: fetched %s index codes", len(self._index_code_list))
            except Exception as e:
                logger.warning("Failed to fetch index codes: %s", e)
                self._index_code_list = []

            self._subscribe_data = ad.SubscribeData()
            self._setup_callbacks()

            self._subscribe_thread = threading.Thread(target=self._run_subscribe, daemon=True)
            self._subscribe_thread.start()

            self.stats["start_time"] = datetime.now().isoformat()
            logger.info("Realtime subscriber started")
            return True

        except Exception as e:
            logger.error("Realtime subscriber initialization failed: %s", e)
            return False

    def shutdown(self) -> None:
        """Stop the subscriber thread."""
        self._running = False
        if self._subscribe_data is not None:
            try:
                # SDK may provide a stop method; if not, rely on thread daemon status
                if hasattr(self._subscribe_data, "stop"):
                    self._subscribe_data.stop()
            except Exception as e:
                logger.warning("Error stopping SubscribeData: %s", e)
        logger.info("Realtime subscriber shutdown")

    # ============================================================
    # Internal
    # ============================================================

    def _run_subscribe(self) -> None:
        """Blocking loop that runs SubscribeData (runs in background thread)."""
        self._running = True
        while self._running:
            try:
                self._subscribe_data.run()
            except Exception as e:
                logger.error("SubscribeData run error: %s", e)
                if self._running:
                    import time

                    time.sleep(5)

    def _setup_callbacks(self) -> None:
        """Register SDK callbacks for snapshot, index snapshot and kline data."""
        import AmazingData as ad

        # Stock snapshot callback (§3.5.3.2)
        @self._subscribe_data.register(code_list=self._code_list, period=ad.constant.Period.snapshot.value)
        def on_snapshot(data, period_val):  # noqa: N806
            self._handle_snapshot(data, period_val)

        # Index snapshot callback (§3.5.3.1 指数实时快照)
        if self._index_code_list:
            @self._subscribe_data.register(code_list=self._index_code_list, period=ad.constant.Period.snapshot.value)
            def on_index_snapshot(data, period_val):  # noqa: N806
                self._handle_index_snapshot(data, period_val)

        # K-line callbacks (min1 by default; configurable via env)
        settings = get_settings()
        kline_periods = getattr(settings, "realtime_kline_periods", ["min1"])
        for period_str in kline_periods:
            period_val = KLINE_PERIOD_MAP.get(period_str)
            if period_val is None:
                logger.warning("Unknown kline period: %s, skipping", period_str)
                continue
            self._register_kline_callback(period_str, period_val)

    def _register_kline_callback(self, period_str: str, period_val: int) -> None:
        @self._subscribe_data.register(code_list=self._code_list, period=period_val)
        def on_kline(data, pval):  # noqa: N806
            self._handle_kline(data, pval, period_str)

    def _handle_snapshot(self, data: Any, period: int) -> None:
        """Process a single tick of snapshot data."""
        try:
            self.stats["total_received"] += 1

            code = self._extract_code(data)
            if not code:
                return

            serialized = self._serialize_data(data)

            # Persist to Redis
            cache = get_cache_manager()
            if cache.set_realtime_market(serialized, REALTIME_QUOTE_KEY, code):
                self.stats["saved_to_redis"] += 1

            # Enqueue for WebSocket broadcast
            subscribers = self.ws_manager.get_subscribers_for_code(code)
            if subscribers and self._loop is not None:
                msg = {
                    "type": "quote",
                    "code": code,
                    "period": period,
                    "data": serialized,
                    "timestamp": datetime.now().isoformat(),
                }
                try:
                    self._broadcast_queue.put_nowait((subscribers, msg))
                except queue.Full:
                    pass

        except Exception as e:
            logger.error("Handle snapshot data error: %s", e)
            self.stats["failed"] += 1

    def _handle_index_snapshot(self, data: Any, period: int) -> None:
        """Process a single tick of index snapshot data (§3.5.3.1)."""
        try:
            self.stats["total_received"] += 1

            code = self._extract_code(data)
            if not code:
                return

            serialized = self._serialize_data(data)

            # Persist to Redis
            cache = get_cache_manager()
            if cache.set_realtime_market(serialized, REALTIME_INDEX_KEY, code):
                self.stats["saved_to_redis"] += 1

            # Enqueue for WebSocket broadcast
            subscribers = self.ws_manager.get_subscribers_for_code(code)
            if subscribers and self._loop is not None:
                msg = {
                    "type": "index",
                    "code": code,
                    "period": period,
                    "data": serialized,
                    "timestamp": datetime.now().isoformat(),
                }
                try:
                    self._broadcast_queue.put_nowait((subscribers, msg))
                except queue.Full:
                    pass

        except Exception as e:
            logger.error("Handle index snapshot data error: %s", e)
            self.stats["failed"] += 1

    def _handle_kline(self, data: Any, period: int, period_str: str) -> None:
        """Process a single tick of kline data."""
        try:
            self.stats["total_received"] += 1

            code = self._extract_code(data)
            if not code:
                return

            serialized = self._serialize_data(data)

            # Persist to Redis
            cache = get_cache_manager()
            if cache.set_realtime_market(serialized, REALTIME_KLINE_KEY, period_str, code):
                self.stats["saved_to_redis"] += 1

            # Enqueue for WebSocket broadcast
            subscribers = self.ws_manager.get_subscribers_for_code(code)
            if subscribers and self._loop is not None:
                msg = {
                    "type": "kline",
                    "code": code,
                    "period": period,
                    "period_str": period_str,
                    "data": serialized,
                    "timestamp": datetime.now().isoformat(),
                }
                try:
                    self._broadcast_queue.put_nowait((subscribers, msg))
                except queue.Full:
                    pass

        except Exception as e:
            logger.error("Handle kline data error: %s", e)
            self.stats["failed"] += 1

    def _extract_code(self, data: Any) -> Optional[str]:
        if hasattr(data, "code"):
            return str(data.code)
        if isinstance(data, dict):
            return str(data.get("code", "")) or None
        return None

    def _serialize_data(self, data: Any) -> Dict[str, Any]:
        if isinstance(data, dict):
            return {k: self._make_serializable(v) for k, v in data.items()}
        result: Dict[str, Any] = {}
        for attr in dir(data):
            if not attr.startswith("_"):
                try:
                    value = getattr(data, attr)
                    if not callable(value):
                        result[attr] = self._make_serializable(value)
                except Exception:
                    pass
        return result

    def _make_serializable(self, value: Any) -> Any:
        if isinstance(value, (str, int, bool, type(None))):
            return value
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return None
            return value
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    # ============================================================
    # Async broadcast loop
    # ============================================================

    async def broadcast_loop(self) -> None:
        """Async loop that drains the broadcast queue and sends to WebSockets."""
        logger.info("WebSocket broadcast loop started")
        while True:
            try:
                batch: List[tuple] = []
                try:
                    while len(batch) < 100:
                        item = self._broadcast_queue.get_nowait()
                        batch.append(item)
                except queue.Empty:
                    pass

                if not batch:
                    await asyncio.sleep(0.05)
                    continue

                for subscribers, msg in batch:
                    disconnected: List[str] = []
                    for client_id in subscribers:
                        ws = self.ws_manager.get_websocket(client_id)
                        if ws is not None:
                            try:
                                await ws.send_json(msg)
                                self.stats["ws_broadcasts"] += 1
                            except Exception:
                                disconnected.append(client_id)

                    for cid in disconnected:
                        self.ws_manager.disconnect(cid)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Broadcast loop error: %s", e)
                await asyncio.sleep(0.1)


# Singleton
_subscriber_instance: Optional[RealtimeSubscriber] = None


def get_realtime_subscriber() -> RealtimeSubscriber:
    """Return the global RealtimeSubscriber singleton."""
    global _subscriber_instance
    if _subscriber_instance is None:
        _subscriber_instance = RealtimeSubscriber()
    return _subscriber_instance
