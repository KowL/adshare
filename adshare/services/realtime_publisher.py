"""Real-time publisher service for Worker server.

Connects to AmazingData SDK, receives tick data, writes to Redis,
and publishes to Redis Pub/Sub for broadcast consumption.
Runs in the Worker service process.
"""

from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from adshare.core.cache import get_cache_manager
from adshare.core.config import get_settings
from adshare.core.logging import get_logger

logger = get_logger(__name__)

# Redis keys (for REST API query)
REALTIME_QUOTE_KEY = "realtime:quote"
REALTIME_KLINE_KEY = "realtime:kline"
REALTIME_INDEX_KEY = "realtime:index"

# Redis Pub/Sub channels (for broadcast)
CHANNEL_QUOTE = "adshare:realtime:quote"
CHANNEL_INDEX = "adshare:realtime:index"
CHANNEL_KLINE_PREFIX = "adshare:realtime:kline:"

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


class RealtimePublisher:
    """Worker-side real-time data publisher.

    - Subscribes to AmazingData SDK tick data
    - Writes to Redis (for REST API queries)
    - Publishes to Redis Pub/Sub (for broadcast consumption)
    - Runs in the Worker service process
    """

    def __init__(self) -> None:
        self._subscribe_data: Optional[Any] = None
        self._base_data: Optional[Any] = None
        self._code_list: List[str] = []
        self._index_code_list: List[str] = []
        self._running = False
        self._subscribe_thread: Optional[threading.Thread] = None

        self.stats: Dict[str, Any] = {
            "total_received": 0,
            "saved_to_redis": 0,
            "published": 0,
            "failed": 0,
            "start_time": None,
        }

    # ============================================================
    # Lifecycle
    # ============================================================

    def initialize(self) -> bool:
        """Login, fetch code list, set up callbacks and start subscriber thread."""
        try:
            from amazingdata_worker.adapters.amazingdata import get_adapter

            adapter = get_adapter()
            if not adapter.ensure_login():
                logger.error(
                    "AmazingData not logged in, cannot start realtime publisher"
                )
                return False

            import AmazingData as ad

            self._base_data = ad.BaseData()
            self._code_list = self._base_data.get_code_list(
                security_type="EXTRA_STOCK_A"
            )
            if not self._code_list:
                self._code_list = ["000001.SZ", "600000.SH", "600519.SH"]
            logger.info(
                "Realtime publisher: fetched %s A-share codes", len(self._code_list)
            )

            # Index codes
            try:
                self._index_code_list = self._base_data.get_code_list(
                    security_type="EXTRA_INDEX_A"
                )
                logger.info(
                    "Realtime publisher: fetched %s index codes",
                    len(self._index_code_list),
                )
            except Exception as e:
                logger.warning("Failed to fetch index codes: %s", e)
                self._index_code_list = []

            self._subscribe_data = ad.SubscribeData()
            self._setup_callbacks()

            self.stats["start_time"] = datetime.now().isoformat()
            logger.info("Realtime publisher initialized (run in caller thread)")
            return True

        except Exception as e:
            logger.error("Realtime publisher initialization failed: %s", e)
            return False

    def run_blocking(self) -> None:
        """Blocking loop that runs SubscribeData in the caller thread.

        Moved from background thread to main thread to avoid GIL issues
        with the AmazingData SDK C extension.
        """
        self._running = True
        while self._running:
            try:
                self._subscribe_data.run()
            except Exception as e:
                logger.error("SubscribeData run error: %s", e)
                if self._running:
                    time.sleep(5)

    def shutdown(self) -> None:
        """Stop the subscriber loop."""
        self._running = False
        if self._subscribe_data is not None:
            try:
                if hasattr(self._subscribe_data, "stop"):
                    self._subscribe_data.stop()
            except Exception as e:
                logger.warning("Error stopping SubscribeData: %s", e)
        logger.info("Realtime publisher shutdown")

    # ============================================================
    # Internal
    # ============================================================

    def _setup_callbacks(self) -> None:
        """Register SDK callbacks for snapshot, index snapshot and kline data."""
        import AmazingData as ad

        # Stock snapshot callback
        @self._subscribe_data.register(
            code_list=self._code_list, period=ad.constant.Period.snapshot.value
        )
        def on_snapshot(data, period_val):  # noqa: N806
            self._handle_snapshot(data, period_val)

        # Index snapshot callback
        if self._index_code_list:

            @self._subscribe_data.register(
                code_list=self._index_code_list,
                period=ad.constant.Period.snapshot.value,
            )
            def on_index_snapshot(data, period_val):  # noqa: N806
                self._handle_index_snapshot(data, period_val)

        # K-line callbacks
        settings = get_settings()
        kline_periods = getattr(settings, "realtime_kline_periods", ["min1"])
        for period_str in kline_periods:
            period_val = KLINE_PERIOD_MAP.get(period_str)
            if period_val is None:
                logger.warning("Unknown kline period: %s, skipping", period_str)
                continue
            self._register_kline_callback(period_str, period_val)

    def _register_kline_callback(self, period_str: str, period_val: int) -> None:
        @self._subscribe_data.register(
            code_list=self._code_list, period=period_val
        )
        def on_kline(data, pval):  # noqa: N806
            self._handle_kline(data, pval, period_str)

    # ============================================================
    # Handlers
    # ============================================================

    def _handle_snapshot(self, data: Any, period: int) -> None:
        """Process a single tick of snapshot data."""
        try:
            self.stats["total_received"] += 1

            code = self._extract_code(data)
            if not code:
                return

            serialized = self._serialize_data(data)

            # 1. Persist to Redis (for REST API queries)
            cache = get_cache_manager()
            if cache.set_realtime_market(serialized, REALTIME_QUOTE_KEY, code):
                self.stats["saved_to_redis"] += 1

            # 2. Publish to Redis Pub/Sub (for broadcast)
            msg = json.dumps(
                {
                    "type": "quote",
                    "code": code,
                    "data": serialized,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            cache.redis.publish(CHANNEL_QUOTE, msg)
            self.stats["published"] += 1

        except Exception as e:
            logger.error("Handle snapshot error: %s", e)
            self.stats["failed"] += 1

    def _handle_index_snapshot(self, data: Any, period: int) -> None:
        """Process a single tick of index snapshot data."""
        try:
            self.stats["total_received"] += 1

            code = self._extract_code(data)
            if not code:
                return

            serialized = self._serialize_data(data)

            # 1. Persist to Redis
            cache = get_cache_manager()
            if cache.set_realtime_market(serialized, REALTIME_INDEX_KEY, code):
                self.stats["saved_to_redis"] += 1

            # 2. Publish to Pub/Sub
            msg = json.dumps(
                {
                    "type": "index",
                    "code": code,
                    "data": serialized,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            cache.redis.publish(CHANNEL_INDEX, msg)
            self.stats["published"] += 1

        except Exception as e:
            logger.error("Handle index snapshot error: %s", e)
            self.stats["failed"] += 1

    def _handle_kline(self, data: Any, period: int, period_str: str) -> None:
        """Process a single tick of kline data."""
        try:
            self.stats["total_received"] += 1

            code = self._extract_code(data)
            if not code:
                return

            serialized = self._serialize_data(data)

            # 1. Persist to Redis
            cache = get_cache_manager()
            if cache.set_realtime_market(
                serialized, REALTIME_KLINE_KEY, period_str, code
            ):
                self.stats["saved_to_redis"] += 1

            # 2. Publish to Pub/Sub
            msg = json.dumps(
                {
                    "type": "kline",
                    "code": code,
                    "period": period_str,
                    "data": serialized,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            cache.redis.publish(f"{CHANNEL_KLINE_PREFIX}{period_str}", msg)
            self.stats["published"] += 1

        except Exception as e:
            logger.error("Handle kline error: %s", e)
            self.stats["failed"] += 1

    # ============================================================
    # Helpers
    # ============================================================

    @staticmethod
    def _extract_code(data: Any) -> Optional[str]:
        if hasattr(data, "code"):
            return str(data.code)
        if isinstance(data, dict):
            return str(data.get("code", "")) or None
        return None

    @staticmethod
    def _serialize_data(data: Any) -> Dict[str, Any]:
        if isinstance(data, dict):
            return {
                k: RealtimePublisher._make_serializable(v) for k, v in data.items()
            }
        result: Dict[str, Any] = {}
        for attr in dir(data):
            if not attr.startswith("_"):
                try:
                    value = getattr(data, attr)
                    if not callable(value):
                        result[attr] = RealtimePublisher._make_serializable(value)
                except Exception:
                    pass
        return result

    @staticmethod
    def _make_serializable(value: Any) -> Any:
        if isinstance(value, (str, int, bool, type(None))):
            return value
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return None
            return value
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)


# Singleton
_publisher_instance: Optional[RealtimePublisher] = None


def get_realtime_publisher() -> RealtimePublisher:
    """Return the global :class:`RealtimePublisher` singleton."""
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = RealtimePublisher()
    return _publisher_instance
