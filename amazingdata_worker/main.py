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


def _init_sdk_login(max_wait_seconds: float = 1800.0) -> bool:
    """Login to AmazingData SDK.

    For accounts with a single concurrent connection, a previous worker
    process may still hold the session on the TGW server.  Instead of
    exiting immediately and letting Docker restart us (which creates more
    zombie sessions), we retry in-process with exponential backoff up to
    ``max_wait_seconds``.
    """
    from amazingdata_worker.adapters.amazingdata import get_adapter

    adapter = get_adapter()
    deadline = time.time() + max_wait_seconds
    delay = 5.0

    while time.time() < deadline:
        try:
            login_ok = adapter.login()
            if login_ok:
                logger.info("AmazingData login successful: %s", adapter.login_info)
                return True
            logger.error("AmazingData login failed, will retry in %.1fs", delay)
        except Exception as e:
            logger.error("AmazingData login error: %s, will retry in %.1fs", e, delay)

        remaining = deadline - time.time()
        if remaining <= 0:
            break
        sleep_for = min(delay, remaining)
        time.sleep(sleep_for)
        delay = min(delay * 2, 60.0)

    logger.error("Failed to login to AmazingData within %.0fs", max_wait_seconds)
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


def _run_reference_sync(target: str) -> None:
    """Run reference data sync directly in a background thread.

    Uses the existing SDK connection (already logged in) with _SDK_CALL_LOCK
    protection.  This matches the original amazingdata project architecture
    where SubscribeData and MarketData share a single connection.
    """
    from adshare.historical.sync import (
        _get_adapter_safe,
        sync_financial,
        sync_shareholder,
        sync_index_component,
    )

    settings = get_settings()
    warehouse = get_warehouse(settings)
    adapter = _get_adapter_safe()

    def _sync():
        try:
            # Financial statement sync is disabled; balance/income/cashflow
            # data is not used and its HDF5 cache consumes several GB.
            if target in ("all", "financial"):
                logger.info("Skipping financial sync (disabled)")
            if target in ("all", "shareholder"):
                logger.info("Starting shareholder sync")
                result = sync_shareholder(
                    batch_size=50,
                    settings=settings,
                    warehouse=warehouse,
                    adapter=adapter,
                )
                logger.info(
                    "sync_shareholder: success=%s rows=%s failed=%s duration=%.2fs",
                    result.success,
                    result.rows,
                    result.failed,
                    result.duration,
                )
            if target in ("all", "index"):
                logger.info("Starting index component sync")
                result = sync_index_component(
                    settings=settings,
                    warehouse=warehouse,
                    adapter=adapter,
                )
                logger.info(
                    "sync_index_component: success=%s rows=%s failed=%s duration=%.2fs",
                    result.success,
                    result.rows,
                    result.failed,
                    result.duration,
                )
        except Exception as e:
            logger.exception("Reference sync failed: %s", e)

    # Run in background thread so the main thread can continue
    thread = threading.Thread(target=_sync, daemon=True, name=f"ref-sync-{target}")
    thread.start()
    logger.info("Reference sync thread started: target=%s thread=%s", target, thread.name)


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

        # Incremental daily kline: pull from the last date already in the warehouse
        # up to today. If the warehouse is empty, fall back to the default 2020 start.
        from datetime import datetime, timedelta
        end_date = int(datetime.now().strftime("%Y%m%d"))
        begin_date = 20200101
        try:
            warehouse.refresh_views()
            row = warehouse.connection.execute("SELECT MAX(date) FROM v_kline_day").fetchone()
            last_date = row[0] if row and row[0] else None
            if last_date:
                # Pull the last date again in case the previous run was partial,
                # then continue to today.
                begin_date = int(last_date)
                logger.info("Incremental daily sync from last warehouse date: %s", begin_date)
        except Exception as e:
            logger.warning("Failed to probe last warehouse date, using default begin_date=20200101: %s", e)

        result = sync_kline_daily(from_date=begin_date, to_date=end_date)
        logger.info("sync_kline_daily: succeeded=%s failed=%s rows=%s duration=%.2fs",
                    result.succeeded, result.failed, result.rows, result.duration)

        # Reference data sync in background thread (shares SDK connection)
        _run_reference_sync("all")
    except Exception:
        logger.exception("Immediate sync failed")


def main() -> int:
    """Worker main entry."""
    setup_logging()
    settings = get_settings()

    realtime_enabled = os.environ.get("REALTIME_ENABLED", "true").lower() in ("true", "1", "yes")
    sync_enabled = os.environ.get("SYNC_SCHEDULE_ENABLED", "true").lower() in ("true", "1", "yes")
    mode_parts = []
    if sync_enabled:
        mode_parts.append("data pull")
    if realtime_enabled:
        mode_parts.append("realtime subscription")
    mode = " + ".join(mode_parts) if mode_parts else "idle"

    logger.info("=" * 50)
    logger.info("AmazingData Worker starting...")
    logger.info("Mode: %s", mode)
    logger.info("SDK: %s", settings.amazingdata_connection_string)
    logger.info("Redis: %s", settings.redis_url)
    logger.info("Warehouse: %s", settings.historical_path)
    logger.info("=" * 50)

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

    # 3. Realtime publisher (run in main thread to avoid GIL issues)
    publisher = None
    realtime_enabled = os.environ.get("REALTIME_ENABLED", "true").lower() in ("true", "1", "yes")
    if realtime_enabled:
        try:
            from adshare.services.realtime_publisher import get_realtime_publisher

            publisher = get_realtime_publisher()
            if publisher.initialize():
                logger.info("Realtime publisher initialized, will run in main thread")
            else:
                publisher = None
        except Exception as e:
            logger.warning("Realtime publisher init failed: %s", e)
            publisher = None
    else:
        logger.info("Realtime publisher disabled by REALTIME_ENABLED=false")

    # Register signal handlers after publisher is available so shutdown
    # can call publisher.shutdown() to interrupt subscribe_data.run().
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

    # 4. Sync scheduler
    _init_sync_scheduler()

    # 5. Optional immediate sync
    _run_once_sync()

    # 6. Main loop — run realtime publisher in main thread
    if publisher is not None:
        logger.info("Worker running (realtime publisher in main thread). "
                    "Press Ctrl+C or send SIGTERM to stop.")
        try:
            publisher.run_blocking()
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
    else:
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
        if publisher is not None:
            publisher.shutdown()
    except Exception:
        pass

    try:
        from amazingdata_worker.adapters.amazingdata import get_adapter
        get_adapter().logout()
        logger.info("AmazingData logged out")
    except Exception as e:
        logger.warning("Logout error: %s", e)

    logger.info("Worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
