"""AmazingData Worker — 独立数据拉取服务.

职责:
- 连接 AmazingData SDK (login)
- 启动实时行情订阅 (snapshot/kline/index) → Redis
- 启动定时同步任务 (日K/周K/月K/代码表/日历) → L3 仓库
- 不对外提供 API，只写 Redis 和本地 Parquet

部署: 必须在 linux/amd64 环境运行 (AmazingData SDK 限制).
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

# Allow running as ``python scripts/amazingdata_worker.py``
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adshare.core.config import get_settings  # noqa: E402
from adshare.core.logging import setup_logging, get_logger  # noqa: E402
from adshare.historical import start_scheduler, shutdown_scheduler  # noqa: E402
from adshare.historical.warehouse import get_warehouse  # noqa: E402

logger = get_logger("amazingdata_worker")

# Global shutdown event
_shutdown_event = threading.Event()


def _signal_handler(signum, frame):  # noqa: ARG001
    logger.info("Received signal %s, shutting down...", signum)
    _shutdown_event.set()


def _init_sdk_login() -> bool:
    """Login to AmazingData SDK."""
    from adshare.adapters.amazingdata import get_adapter

    adapter = get_adapter()
    try:
        login_ok = adapter.login()
        if login_ok:
            logger.info("AmazingData login successful: %s", adapter.login_info)
            return True
        logger.error("AmazingData login failed")
        return False
    except Exception as e:
        logger.error("AmazingData login error: %s", e)
        return False


def _init_realtime_subscriber() -> bool:
    """Start realtime subscriber (push to Redis)."""
    realtime_enabled = os.environ.get("REALTIME_ENABLED", "true").lower() in ("true", "1", "yes")
    if not realtime_enabled:
        logger.info("Realtime subscriber disabled by REALTIME_ENABLED=false")
        return False

    try:
        import asyncio
        from adshare.services.realtime import get_realtime_subscriber

        subscriber = get_realtime_subscriber()
        if not subscriber.initialize():
            logger.error("Realtime subscriber initialization failed")
            return False

        # Start broadcast loop in a background thread
        loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(subscriber.broadcast_loop())

        broadcast_thread = threading.Thread(target=_run_loop, daemon=True)
        broadcast_thread.start()

        logger.info("Realtime subscriber started (codes=%s, index=%s)",
                    len(subscriber._code_list), len(subscriber._index_code_list))
        return True
    except Exception as e:
        logger.error("Realtime subscriber init error: %s", e)
        return False


def _init_sync_scheduler() -> bool:
    """Start APScheduler for periodic sync to L3 warehouse."""
    sync_enabled = os.environ.get("SYNC_SCHEDULE_ENABLED", "true").lower() in ("true", "1", "yes")
    if not sync_enabled:
        logger.info("Sync scheduler disabled by SYNC_SCHEDULE_ENABLED=false")
        return False

    try:
        start_scheduler()
        logger.info("Sync scheduler started")
        return True
    except Exception as e:
        logger.error("Sync scheduler init error: %s", e)
        return False


def _run_once_sync() -> None:
    """Run an immediate sync if SYNC_ON_START is set."""
    if os.environ.get("SYNC_ON_START", "").lower() not in ("true", "1", "yes"):
        return

    logger.info("Running immediate sync on start...")
    try:
        from adshare.historical.sync import sync_meta_codes, sync_meta_calendar, sync_kline_daily

        settings = get_settings()
        warehouse = get_warehouse(settings)

        # Sync meta
        result = sync_meta_codes()
        logger.info("sync_meta_codes: success=%s rows=%s", result.success, result.rows)

        result = sync_meta_calendar()
        logger.info("sync_meta_calendar: success=%s rows=%s", result.success, result.rows)

        # Sync daily kline (last 30 days by default)
        from datetime import datetime, timedelta
        end_date = int(datetime.now().strftime("%Y%m%d"))
        begin_date = int((datetime.now() - timedelta(days=30)).strftime("%Y%m%d"))

        result = sync_kline_daily(from_date=begin_date, to_date=end_date)
        logger.info("sync_kline_daily: succeeded=%s failed=%s rows=%s duration=%.2fs",
                    result.succeeded, result.failed, result.rows, result.duration)
    except Exception as e:
        logger.error("Immediate sync failed: %s", e)


def main() -> int:
    """Worker main entry."""
    setup_logging()
    settings = get_settings()

    logger.info("=" * 50)
    logger.info("AmazingData Worker starting...")
    logger.info("Mode: data pull + realtime subscription")
    logger.info("SDK: %s", settings.amazingdata_connection_string)
    logger.info("Redis: %s", settings.redis_url)
    logger.info("Warehouse: %s", settings.historical_path)
    logger.info("=" * 50)

    # Signal handlers
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # 1. SDK login
    if not _init_sdk_login():
        logger.error("Failed to login to AmazingData, exiting")
        return 1

    # 2. L3 warehouse init
    try:
        if settings.historical_enabled:
            warehouse = get_warehouse(settings)
            health = warehouse.health()
            logger.info("Historical warehouse ready: root=%s duckdb=%s",
                        health["root"], health["duckdb_connected"])
        else:
            logger.info("Historical warehouse disabled")
    except Exception as e:
        logger.warning("Historical warehouse init failed: %s", e)

    # 3. Realtime subscriber
    _init_realtime_subscriber()

    # 4. Sync scheduler
    _init_sync_scheduler()

    # 5. Optional immediate sync
    _run_once_sync()

    # 6. Main loop — keep process alive
    logger.info("Worker running. Press Ctrl+C or send SIGTERM to stop.")
    try:
        while not _shutdown_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")

    # Shutdown
    logger.info("Shutting down worker...")
    shutdown_scheduler()

    try:
        from adshare.services.realtime import get_realtime_subscriber
        get_realtime_subscriber().shutdown()
    except Exception:
        pass

    try:
        from adshare.adapters.amazingdata import get_adapter
        get_adapter().logout()
        logger.info("AmazingData logged out")
    except Exception as e:
        logger.warning("Logout error: %s", e)

    logger.info("Worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
