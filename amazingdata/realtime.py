"""盘中模式: realtime subscription -> Redis + Pub/Sub.

启动:
    python -m amazingdata.realtime

镜像:
    amazingdata-realtime  (FROM amazingdata-base)

Docker:
    docker compose -f amazingdata/docker-compose.realtime.yml up -d

职责:
- 登录 AmazingData SDK
- 启动 RealtimePublisher（订阅 snapshot / index / kline）
- 写入 Redis（供 REST API 读）和 Redis Pub/Sub（供 SSE/WS 广播）
- 阻塞主循环，按 SIGTERM/SIGINT 优雅退出

TGW 单连接账户约束:
- 此服务独占一个 SDK 会话
- 同一主机上 batch 服务的 SDK 会话必须互斥（通过外部调度切换容器）
"""

from __future__ import annotations

import json
import math
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow running as ``python amazingdata/realtime.py``
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adshare.core.cache import get_cache_manager  # noqa: E402
from amazingdata.config import get_worker_settings  # noqa: E402
from adshare.core.logging import setup_logging, get_logger  # noqa: E402
from adshare.core.realtime_keys import (  # noqa: E402
    CHANNEL_INDEX,
    CHANNEL_KLINE_PREFIX,
    CHANNEL_QUOTE,
    REALTIME_INDEX_KEY,
    REALTIME_KLINE_HIST_KEY,
    REALTIME_KLINE_KEY,
    REALTIME_QUOTE_KEY,
)

from amazingdata.adapters.amazingdata import get_adapter  # noqa: E402
from amazingdata.adapters.base import DataSourceAdapter, SubscriptionSource  # noqa: E402

logger = get_logger("amazingdata.realtime")

_shutdown_event = threading.Event()


# ============================================================
# SDK login (with retry for TGW single-connection accounts)
# ============================================================

def _init_sdk_login(max_wait_seconds: float = 1800.0) -> bool:
    """Login to AmazingData SDK with exponential backoff.

    For accounts with a single concurrent connection, a previous worker
    process may still hold the session on the TGW server. Instead of
    exiting immediately, we retry in-process up to ``max_wait_seconds``.
    """
    adapter = get_adapter()
    deadline = time.time() + max_wait_seconds
    delay = 5.0
    while time.time() < deadline:
        try:
            if adapter.login():
                logger.info("AmazingData login successful: %s", adapter.login_info)
                return True
            logger.error("AmazingData login failed, will retry in %.1fs", delay)
        except Exception as e:
            logger.error("AmazingData login error: %s, will retry in %.1fs", e, delay)
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, 60.0)
    logger.error("Failed to login to AmazingData within %.0fs", max_wait_seconds)
    return False


# ============================================================
# RealtimePublisher
# ============================================================

class RealtimePublisher:
    """Realtime data publisher (worker-side).

    - Subscribes to realtime ticks via the data-source adapter
    - Writes to Redis (for REST API queries)
    - Publishes to Redis Pub/Sub (for broadcast consumption)
    """

    def __init__(self) -> None:
        self._adapter: Optional[DataSourceAdapter] = None
        self._subscribe_data: Optional[SubscriptionSource] = None
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

    def _load_cached_codes(
        self,
        suffixes: Tuple[str, ...] = (".SH", ".SZ"),
        fallback: Optional[List[str]] = None,
    ) -> List[str]:
        """Load codes from the cached ``meta/codes.parquet`` file.

        This avoids creating a BaseData connection, which is critical when
        the TGW account only allows a single concurrent connection that is
        already used by SubscribeData.
        """
        try:
            root = Path(get_worker_settings().historical_path).resolve()
            path = root / "meta" / "codes.parquet"
            if not path.exists():
                logger.warning("Cached codes file not found: %s", path)
                return fallback or []

            import pandas as pd

            df = pd.read_parquet(path)
            if df is None or df.empty or "code" not in df.columns:
                return fallback or []

            codes = df["code"].dropna().astype(str).tolist()
            codes = [c for c in codes if any(c.endswith(s) for s in suffixes)]
            if not codes:
                return fallback or []
            return codes
        except Exception as e:
            logger.warning("Failed to load cached codes: %s", e)
            return fallback or []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """Login, fetch code list, set up callbacks and prepare subscriber."""
        try:
            adapter = get_adapter()
            if not adapter.ensure_login():
                logger.error("Data source not logged in, cannot start realtime publisher")
                return False

            # Load the full A-share code list directly from the SDK's
            # daily-fresh code table. ``EXTRA_STOCK_A_SH_SZ`` = SH/SZ
            # A-shares only (BJ excluded, per project data scope).
            # Fall back to the cached meta/codes.parquet (maintained by
            # batch.sync_meta_codes) if the SDK call fails.
            try:
                sdk_codes = adapter.get_code_list("EXTRA_STOCK_A_SH_SZ")
                self._code_list = [
                    c for c in sdk_codes
                    if any(c.endswith(s) for s in (".SH", ".SZ"))
                ]
                if not self._code_list:
                    raise RuntimeError("SDK returned empty A-share code list")
                logger.info(
                    "Realtime publisher: loaded %s A-share codes from SDK",
                    len(self._code_list),
                )
            except Exception as e:
                logger.warning(
                    "Failed to fetch A-share codes from SDK (%s); "
                    "falling back to cached meta/codes.parquet", e,
                )
                self._code_list = self._load_cached_codes(
                    suffixes=(".SH", ".SZ"),
                    fallback=["000001.SZ", "600000.SH", "600519.SH"],
                )
                logger.info(
                    "Realtime publisher: loaded %s A-share codes from cache",
                    len(self._code_list),
                )

            try:
                self._index_code_list = adapter.get_code_list("EXTRA_INDEX_A")
                logger.info(
                    "Realtime publisher: fetched %s index codes",
                    len(self._index_code_list),
                )
            except Exception as e:
                logger.warning("Failed to fetch index codes: %s", e)
                self._index_code_list = [
                    "000001.SH",  # 上证指数
                    "399001.SZ",  # 深证成指
                    "399006.SZ",  # 创业板指
                    "000016.SH",  # 上证50
                    "000300.SH",  # 沪深300
                    "000905.SH",  # 中证500
                    "000688.SH",  # 科创50
                ]

            self._adapter = adapter
            self._subscribe_data = adapter.create_subscription_source()
            self._setup_callbacks()

            self.stats["start_time"] = datetime.now().isoformat()
            logger.info("Realtime publisher initialized (run in caller thread)")
            return True
        except Exception as e:
            logger.error("Realtime publisher initialization failed: %s", e)
            return False

    def run_blocking(self) -> None:
        """Blocking loop that runs SubscribeData in the caller thread."""
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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _setup_callbacks(self) -> None:
        assert self._adapter is not None and self._subscribe_data is not None
        snapshot_period = self._adapter.period_value("snapshot")

        @self._subscribe_data.register(
            code_list=self._code_list, period=snapshot_period
        )
        def on_snapshot(data, period_val):  # noqa: N806
            self._handle_snapshot(data, period_val)

        if self._index_code_list:
            @self._subscribe_data.register(
                code_list=self._index_code_list,
                period=snapshot_period,
            )
            def on_index_snapshot(data, period_val):  # noqa: N806
                self._handle_index_snapshot(data, period_val)

        settings = get_worker_settings()
        kline_periods = getattr(settings, "realtime_kline_periods", ["min1"])
        for period_str in kline_periods:
            try:
                period_val = self._adapter.period_value(period_str)
            except ValueError:
                logger.warning("Unknown kline period: %s, skipping", period_str)
                continue
            self._register_kline_callback(period_str, period_val)

    def _register_kline_callback(self, period_str: str, period_val: int) -> None:
        @self._subscribe_data.register(
            code_list=self._code_list, period=period_val
        )
        def on_kline(data, pval):  # noqa: N806
            self._handle_kline(data, pval, period_str)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_snapshot(self, data: Any, period: int) -> None:
        try:
            self.stats["total_received"] += 1
            code = self._extract_code(data)
            if not code:
                return
            serialized = self._serialize_data(data)
            cache = get_cache_manager()
            if cache.set_realtime_market(serialized, REALTIME_QUOTE_KEY, code):
                self.stats["saved_to_redis"] += 1
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
        try:
            self.stats["total_received"] += 1
            code = self._extract_code(data)
            if not code:
                return
            serialized = self._serialize_data(data)
            cache = get_cache_manager()
            if cache.set_realtime_market(serialized, REALTIME_INDEX_KEY, code):
                self.stats["saved_to_redis"] += 1
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
        try:
            self.stats["total_received"] += 1
            code = self._extract_code(data)
            if not code:
                return
            serialized = self._serialize_data(data)
            cache = get_cache_manager()
            if cache.set_realtime_market(
                serialized, REALTIME_KLINE_KEY, period_str, code
            ):
                self.stats["saved_to_redis"] += 1
            self._append_kline_stream(cache, code, period_str, serialized)
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

    def _append_kline_stream(
        self,
        cache: Any,
        code: str,
        period_str: str,
        serialized: Dict[str, Any],
    ) -> None:
        """Accumulate the bar into the per-code+freq Redis Stream.

        The Stream backs the tushare ``rt_min`` endpoint (recent N bars);
        the single-key SETEX above is kept unchanged for
        ``/realtime/kline/{code}``.
        """
        try:
            settings = get_worker_settings()
            stream_key = cache._make_key(
                "realtime", f"{REALTIME_KLINE_HIST_KEY}:{period_str}", code
            )
            cache.redis.xadd(
                stream_key,
                {
                    "trade_time": self._kline_time_ms(serialized),
                    "data": json.dumps(serialized),
                },
                maxlen=settings.realtime_kline_max_bars,
                approximate=True,
            )
            cache.redis.expire(stream_key, settings.realtime_kline_history_ttl)
        except Exception as e:
            logger.error("Append kline stream error (%s %s): %s", code, period_str, e)

    @staticmethod
    def _kline_time_ms(serialized: Dict[str, Any]) -> int:
        """Extract the bar time as epoch milliseconds from a serialized kline."""
        raw = serialized.get("kline_time") or serialized.get("trade_time")
        if raw:
            try:
                return int(datetime.fromisoformat(str(raw)).timestamp() * 1000)
            except (ValueError, TypeError, OverflowError):
                pass
        return int(time.time() * 1000)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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


# ============================================================
# Entry point
# ============================================================

def main() -> int:
    setup_logging()
    settings = get_worker_settings()
    logger.info("=" * 50)
    logger.info("AmazingData Realtime starting...")
    logger.info("SDK: %s", settings.amazingdata_connection_string)
    logger.info("Redis: %s", settings.redis_url)
    logger.info("=" * 50)

    if not _init_sdk_login():
        logger.error("Failed to login to AmazingData, exiting")
        return 1

    publisher = None
    try:
        publisher = get_realtime_publisher()
        if not publisher.initialize():
            logger.error("Realtime publisher init failed")
            return 1
    except Exception as e:
        logger.exception("Realtime publisher init error: %s", e)
        return 1

    def _signal_handler(signum, frame):  # noqa: ARG001
        logger.info("Received signal %s, shutting down...", signum)
        _shutdown_event.set()
        if publisher is not None:
            try:
                publisher.shutdown()
            except Exception:
                pass

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        logger.info("Realtime publisher running in main thread. "
                    "Press Ctrl+C or send SIGTERM to stop.")
        publisher.run_blocking()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")

    logger.info("Shutting down realtime...")
    try:
        publisher.shutdown()
    except Exception:
        pass

    try:
        get_adapter().logout()
        logger.info("AmazingData logged out")
    except Exception as e:
        logger.warning("Logout error: %s", e)

    logger.info("Realtime stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
