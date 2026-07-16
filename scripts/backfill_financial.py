"""Backfill financial reference data into the L3 warehouse.

Runs directly in the current process using the existing SDK connection
with _SDK_CALL_LOCK protection.  This matches the original amazingdata
project architecture where all SDK calls share a single connection.

Usage::

    docker compose run --rm amazingdata-worker python scripts/backfill_financial.py
    docker compose run --rm amazingdata-worker python scripts/backfill_financial.py --type balance
    docker compose run --rm amazingdata-worker python scripts/backfill_financial.py --resume
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adshare.core.config import get_settings
from adshare.core.logging import setup_logging, get_logger
from amazingdata_worker.sync import sync_financial
from adshare.historical.warehouse import get_warehouse
from amazingdata_worker.adapters.amazingdata import get_adapter

logger = get_logger("backfill_financial")

# ---------------------------------------------------------------------------
# Resume state (written to the warehouse so it survives container restarts)
# ---------------------------------------------------------------------------

STATE_FILE = Path("/app/data/.backfill_financial_state.json")


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Direct batch worker — runs in the same process
# ---------------------------------------------------------------------------

def _run_batch(statement_type: str, batch_size: int, offset: int, codes: List[str]) -> tuple[int, int, float]:
    """Run one batch directly using the existing SDK connection."""
    t0 = time.time()
    try:
        settings = get_settings()
        warehouse = get_warehouse(settings)
        adapter = get_adapter()

        result = sync_financial(
            statement_type=statement_type,
            batch_size=batch_size,
            offset=offset,
            merge=True,
            settings=settings,
            warehouse=warehouse,
            adapter=adapter,
        )
        elapsed = time.time() - t0
        return result.rows, result.failed, elapsed
    except Exception as e:
        elapsed = time.time() - t0
        logger.error("Batch failed: type=%s offset=%s error=%s", statement_type, offset, e)
        return 0, batch_size, elapsed


# ---------------------------------------------------------------------------
# Main backfill loop
# ---------------------------------------------------------------------------

def _all_codes() -> List[str]:
    from amazingdata_worker.sync import sync_meta_codes
    from adshare.core.config import get_settings
    from adshare.historical.warehouse import get_warehouse
    settings = get_settings()
    warehouse = get_warehouse(settings)
    codes_path = warehouse.root / "meta" / "codes.parquet"
    if not codes_path.exists():
        sync_meta_codes()
    import pandas as pd
    df = pd.read_parquet(codes_path)
    return df["code"].tolist()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=("balance", "income", "cashflow", "all"), default="all")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--resume", action="store_true", help="Resume from last recorded offset")
    parser.add_argument("--offset", type=int, default=0, help="Start at this code offset (0-based)")
    parser.add_argument("--limit", type=int, default=None, help="Stop after this many codes")
    args = parser.parse_args()

    setup_logging()
    settings = get_settings()

    if not settings.historical_enabled:
        print("❌ HISTORICAL_ENABLED is false; refusing to backfill.")
        return 1

    # Financial statement backfill is disabled; balance/income/cashflow
    # data is not used and its HDF5 cache consumes several GB.
    print("⚠️ Financial statement backfill is disabled")
    return 0

    # Login to SDK (single connection for all batches)
    adapter = get_adapter()
    if not adapter.login():
        print("❌ AmazingData login failed")
        return 1
    print("✅ AmazingData login successful")

    codes = _all_codes()
    if args.limit:
        codes = codes[: args.limit]
    total = len(codes)
    print(f"📦 {total} codes to backfill")

    state = _load_state()
    types = ["balance", "income", "cashflow"] if args.type == "all" else [args.type]

    for statement_type in types:
        key = f"{statement_type}_offset"
        offset = state.get(key, 0) if args.resume else args.offset
        print(f"\n=== {statement_type.upper()} ===  offset={offset}/{total}")

        while offset < total:
            batch_codes = codes[offset : offset + args.batch_size]
            print(f"  batch {offset}/{total}  ({len(batch_codes)} codes) ...", end=" ", flush=True)

            rows, failed, elapsed = _run_batch(statement_type, args.batch_size, offset, codes)

            if rows == 0 and failed == args.batch_size:
                print(f"FAILED  ({elapsed:.1f}s)")
                # Retry same batch after a short sleep
                print("  → retrying same batch in 10s ...")
                time.sleep(10)
            else:
                print(f"ok  rows={rows} failed={failed} ({elapsed:.1f}s)")
                offset += args.batch_size
                state[key] = offset
                _save_state(state)

        print(f"=== {statement_type.upper()} DONE ===")
        state[key] = total
        _save_state(state)

    print("\n🎉 All financial backfill complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
