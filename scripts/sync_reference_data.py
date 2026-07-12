"""One-off reference data sync script.

Run from the project root with:
    docker compose run --rm amazingdata-worker python scripts/sync_reference_data.py [all|balance|income|cashflow|shareholder|index]

Or directly inside the worker container.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adshare.core.config import get_settings
from adshare.core.logging import setup_logging, get_logger
from adshare.historical.warehouse import get_warehouse
from adshare.historical.sync import (
    sync_financial,
    sync_shareholder,
    sync_index_component,
)
from amazingdata_worker.adapters.amazingdata import get_adapter

logger = get_logger("sync_reference_data")


def _sync_financial_all(settings, warehouse, adapter):
    # Financial statement sync is disabled; balance/income/cashflow
    # data is not used and its HDF5 cache consumes several GB.
    logger.info("Skipping financial sync (disabled)")


def _sync_shareholder(settings, warehouse, adapter):
    logger.info("Syncing shareholder numbers")
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


def _sync_index(settings, warehouse, adapter):
    logger.info("Syncing index components")
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


def main() -> int:
    setup_logging()
    settings = get_settings()
    target = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()

    logger.info("=" * 50)
    logger.info("Starting one-off reference data sync: %s", target)
    logger.info("=" * 50)

    adapter = get_adapter()
    if not adapter.login():
        logger.error("AmazingData login failed")
        return 1

    warehouse = get_warehouse(settings)
    logger.info("Warehouse ready: %s", warehouse.root)

    handlers = {
        "all": [_sync_financial_all, _sync_shareholder, _sync_index],
        "balance": [_sync_financial_all],
        "income": [_sync_financial_all],
        "cashflow": [_sync_financial_all],
        "financial": [_sync_financial_all],
        "shareholder": [_sync_shareholder],
        "index": [_sync_index],
    }

    chosen = handlers.get(target)
    if chosen is None:
        logger.error("Unknown sync target: %s", target)
        return 1

    for handler in chosen:
        handler(settings, warehouse, adapter)

    logger.info("Reference data sync completed: %s", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
