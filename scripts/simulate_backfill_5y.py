"""Demonstrate the 5-year K-line backfill end-to-end with a mock SDK.

This script wires the same code paths the production ``backfill_kline.py``
uses, but substitutes a deterministic in-memory SDK so the run is
reproducible on any machine (including ARM Macs where the real
AmazingData wheel cannot be installed).

Run::

    python scripts/simulate_backfill_5y.py

It writes Parquet files into ``./data`` and prints summary
statistics when it finishes.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Allow running from any working directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Make sure historical features are enabled
os.environ.setdefault("HISTORICAL_ENABLED", "true")
os.environ.setdefault("SYNC_SCHEDULE_ENABLED", "false")

from unittest.mock import MagicMock  # noqa: E402

from adshare.core.config import get_settings  # noqa: E402
from amazingdata_worker import sync as hist_sync  # noqa: E402
from adshare.historical.warehouse import get_warehouse  # noqa: E402

CODES = ["000001.SZ", "600000.SH", "300750.SZ", "688981.SH", "830799.BJ"]
FROM_DATE = 20210101
TO_DATE = 20251231  # inclusive 5-year window


def _synth_kline(code: str, begin: int, end: int, period: str) -> "pd.DataFrame":
    import pandas as pd

    step = {"day": 1, "week": 5, "month": 30}.get(period, 1)
    base = sum(ord(c) for c in code) % 50 + 10
    start = datetime.strptime(str(begin), "%Y%m%d")
    last = datetime.strptime(str(end), "%Y%m%d")
    rows = []
    n = 0
    cur = start
    while cur <= last:
        if step == 1 and cur.weekday() >= 5:
            cur += timedelta(days=1)
            continue
        rows.append({
            "date": int(cur.strftime("%Y%m%d")),
            "open": base + (n % 30) * 0.05,
            "high": base + (n % 30) * 0.05 + 0.5,
            "low": base + (n % 30) * 0.05 - 0.4,
            "close": base + (n % 30) * 0.05 + 0.1,
            "volume": 100_000 + (n % 100) * 1_000,
            "amount": float(100_000 * (base + (n % 30) * 0.05)),
        })
        cur += timedelta(days=step)
        n += 1
    return pd.DataFrame(rows)


def _build_mock_sdk() -> MagicMock:
    mock = MagicMock()
    mock.get_code_list.return_value = CODES
    mock.get_code_info.return_value = __import__("pandas").DataFrame(
        {"symbol": [f"Stock {c}" for c in CODES]}, index=CODES
    )
    mock.get_calendar.return_value = __import__("pandas").DataFrame(
        {"date": [int(datetime(2021, 1, 4).strftime("%Y%m%d"))]}
    )

    def _kline(codes, begin_date, end_date, period, **kwargs):
        return _synth_kline(codes.strip(), begin_date, end_date, period).assign(
            code=codes.strip()
        )

    mock.get_kline.side_effect = _kline
    return mock


def main() -> int:
    settings = get_settings()
    warehouse = get_warehouse(settings)
    print(f"📦 Warehouse root: {warehouse.root}")

    mock = _build_mock_sdk()
    started = time.time()
    total_rows = 0
    failures = 0

    for period_label, period_alias, func in [
        ("daily", "day", hist_sync.sync_kline_daily),
        ("weekly", "week", hist_sync.sync_kline_weekly),
        ("monthly", "month", hist_sync.sync_kline_monthly),
    ]:
        t0 = time.time()
        result = func(
            from_date=FROM_DATE,
            to_date=TO_DATE,
            codes=CODES,
            settings=settings,
            warehouse=warehouse,
            adapter=mock,
        )
        duration = time.time() - t0
        print(
            f"  • {period_label} range=[{FROM_DATE},{TO_DATE}]: "
            f"succeeded={result.succeeded} failed={result.failed} "
            f"rows={result.rows} duration={duration:.2f}s"
        )
        total_rows += result.rows
        failures += result.failed

    # Meta files
    print("\n📋 Meta sync")
    res_codes = hist_sync.sync_meta_codes(settings=settings, warehouse=warehouse, adapter=mock)
    print(f"  • codes.parquet: rows={res_codes.rows} success={res_codes.success}")
    res_cal = hist_sync.sync_meta_calendar(
        market="SH", settings=settings, warehouse=warehouse, adapter=mock
    )
    print(f"  • calendar.parquet: rows={res_cal.rows} success={res_cal.success}")

    stats = warehouse.stats()
    duration = time.time() - started
    print("\n=== 5-year simulation complete ===")
    print(f"   total duration: {duration:.2f}s")
    print(f"   total_rows written: {total_rows}")
    print(f"   failures: {failures}")
    print(
        f"   daily file count: {stats['periods']['daily']['file_count']} "
        f"({stats['periods']['daily']['total_bytes']/1024:.1f} KiB)"
    )
    print(
        f"   weekly file count: {stats['periods']['weekly']['file_count']} "
        f"({stats['periods']['weekly']['total_bytes']/1024:.1f} KiB)"
    )
    print(
        f"   monthly file count: {stats['periods']['monthly']['file_count']} "
        f"({stats['periods']['monthly']['total_bytes']/1024:.1f} KiB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
