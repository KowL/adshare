"""End-to-end integration test for the 5-year K-line backfill.

This test simulates the full backfill flow described in
``scripts/backfill_kline.py`` by providing a mock SDK adapter that
generates 5 years of synthetic K-line data for a small set of codes,
then verifies the warehouse stores and queries the data correctly.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock

import pandas as pd
import pytest

from adshare.historical import sync as hist_sync
from adshare.historical import warehouse as hist_warehouse
from adshare.historical.models import (
    KLINE_COLUMNS,
    kline_file_path,
    normalize_period,
)


def _make_synthetic_kline(
    code: str,
    begin_date: int,
    end_date: int,
    period: str,
) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame spanning ``[begin_date, end_date]``.

    For ``period == "day"`` we produce one row per business day. For weekly
    and monthly we downsample by sampling roughly every 5 or 30 rows.
    """
    step_days = {"day": 1, "week": 5, "month": 30}.get(period, 1)
    start = datetime.strptime(str(begin_date), "%Y%m%d")
    end = datetime.strptime(str(end_date), "%Y%m%d")
    rows = []
    n = 0
    cur = start
    base_price = 10.0 if code.startswith("000001") else 20.0
    while cur <= end:
        # Skip weekends for daily to keep the test realistic
        if step_days == 1 and cur.weekday() >= 5:
            cur += timedelta(days=1)
            continue
        d_int = int(cur.strftime("%Y%m%d"))
        price = base_price + (n % 50) * 0.05
        rows.append({
            "date": d_int,
            "open": price,
            "high": price + 0.5,
            "low": price - 0.4,
            "close": price + 0.1,
            "volume": 100_000 + (n % 100) * 1_000,
            "amount": float(100_000 * price),
        })
        cur += timedelta(days=step_days)
        n += 1
    return pd.DataFrame(rows)


class FakeSDK:
    """A minimal mock SDK that returns 5 years of synthetic K-line data."""

    def __init__(self, codes: List[str], year_start: int, year_end: int):
        self.codes = codes
        self.year_start = year_start
        self.year_end = year_end
        self.call_count = 0

    def get_code_list(self, security_type: str = "EXTRA_STOCK_A_SH_SZ") -> List[str]:
        return list(self.codes)

    def get_code_info(self, security_type: str = "EXTRA_STOCK_A") -> pd.DataFrame:
        return pd.DataFrame(
            {"symbol": [f"Stock {c}" for c in self.codes]},
            index=self.codes,
        )

    def get_calendar(self, market: str = "SH") -> pd.DataFrame:
        dates = []
        cur = datetime(self.year_start, 1, 1)
        end = datetime(self.year_end, 12, 31)
        while cur <= end:
            if cur.weekday() < 5:
                dates.append(int(cur.strftime("%Y%m%d")))
            cur += timedelta(days=1)
        return pd.DataFrame({"date": dates[:50]})  # truncated for speed

    def get_kline(
        self,
        codes: str,
        begin_date: int,
        end_date: int,
        period: str = "day",
        **kwargs,
    ) -> pd.DataFrame:
        self.call_count += 1
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        frames = []
        for c in code_list:
            df = _make_synthetic_kline(c, begin_date, end_date, period)
            if not df.empty:
                df = df.copy()
                df["code"] = c
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)


@pytest.fixture
def five_year_settings(monkeypatch, tmp_path):
    """Settings with a temporary warehouse root and short retry/worker values."""
    env = {
        "HISTORICAL_ENABLED": "true",
        "HISTORICAL_PATH": str(tmp_path / "historical"),
        "DUCKDB_MODE": "memory",
        "DUCKDB_FILE_PATH": str(tmp_path / "duckdb" / "adshare.duckdb"),
        "SYNC_SCHEDULE_ENABLED": "false",
        "SYNC_WORKERS": "2",
        "SYNC_RETRY_ATTEMPTS": "1",
        "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": "16379",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from adshare.core.config import get_settings as _gs
    _gs.cache_clear()
    hist_warehouse.reset_warehouse()
    yield _gs()
    hist_warehouse.reset_warehouse()


def test_5year_backfill_daily(five_year_settings):
    """Simulate a 5-year daily backfill and verify the warehouse contents."""
    codes = ["000001.SZ", "600000.SH", "300750.SZ"]
    years = [2021, 2022, 2023, 2024, 2025]
    fake = FakeSDK(codes, year_start=2021, year_end=2025)
    wh = hist_warehouse.get_warehouse(five_year_settings)

    result = hist_sync.sync_kline_daily(
        from_date=20210101,
        to_date=20251231,
        codes=codes,
        settings=five_year_settings,
        warehouse=wh,
        adapter=fake,
    )
    assert result.success, f"sync failed: {result.errors}"
    assert result.succeeded == 3

    # Verify each code has a flat Parquet file under daily/.
    daily_dir = wh.root / "A_share" / "daily"
    for code in codes:
        path = kline_file_path(wh.root, "day", code)
        assert path.exists(), f"missing flat file for {code}"
        df = pd.read_parquet(path)
        for col in KLINE_COLUMNS:
            assert col in df.columns
        # All 5 years present
        assert df["date"].min() >= 20210101
        assert df["date"].max() <= 20251231
        df_year = df["date"].apply(lambda d: int(str(d)[:4]))
        for year in years:
            assert year in set(df_year), f"missing year {year} in {code}"

    # Per-period metadata sidecar
    meta_path = daily_dir / "_metadata.json"
    assert meta_path.exists()
    payload = json.loads(meta_path.read_text())
    assert payload["file_count"] == 3
    assert payload["first_date"] == 20210101
    assert payload["last_date"] == 20251231
    assert "year" not in payload  # flat layout: no per-year metadata

    # Query via DuckDB
    wh.refresh_views()
    df = wh.query_kline(codes, 20210101, 20251231, "day")
    assert len(df) > 0
    assert set(df["code"].unique()) == set(codes)
    assert df["date"].min() >= 20210101
    assert df["date"].max() <= 20251231
    df_year = df["date"].apply(lambda d: int(str(d)[:4]))
    for year in years:
        assert year in set(df_year), f"missing year {year}"


def test_5year_backfill_weekly(five_year_settings):
    """Verify weekly K-line backfill works for 5 years."""
    codes = ["000001.SZ", "600000.SH"]
    fake = FakeSDK(codes, year_start=2021, year_end=2025)
    wh = hist_warehouse.get_warehouse(five_year_settings)

    result = hist_sync.sync_kline_weekly(
        from_date=20210101,
        to_date=20251231,
        codes=codes,
        settings=five_year_settings,
        warehouse=wh,
        adapter=fake,
    )
    assert result.success
    assert result.succeeded == 2
    for code in codes:
        assert (wh.root / "A_share" / "weekly" / f"{code}.parquet").exists()

    # Verify is_synced across multiple years
    wh.refresh_views()
    assert wh.is_synced(20210101, 20251231, "week", codes)


def test_5year_backfill_monthly(five_year_settings):
    """Verify monthly K-line backfill works for 5 years."""
    codes = ["000001.SZ"]
    fake = FakeSDK(codes, year_start=2021, year_end=2025)
    wh = hist_warehouse.get_warehouse(five_year_settings)

    result = hist_sync.sync_kline_monthly(
        from_date=20210101,
        to_date=20251231,
        codes=codes,
        settings=five_year_settings,
        warehouse=wh,
        adapter=fake,
    )
    assert result.success
    assert (wh.root / "A_share" / "monthly" / f"{codes[0]}.parquet").exists()

    wh.refresh_views()
    assert wh.is_synced(20210101, 20251231, "month", codes)


def test_5year_meta_sync(five_year_settings):
    """Verify the meta sync (codes and calendar) for a 5-year period."""
    codes = ["000001.SZ", "600000.SH", "300750.SZ", "688981.SH", "830799.BJ"]
    fake = FakeSDK(codes, year_start=2021, year_end=2025)
    wh = hist_warehouse.get_warehouse(five_year_settings)

    result_codes = hist_sync.sync_meta_codes(
        settings=five_year_settings, warehouse=wh, adapter=fake
    )
    assert result_codes.success
    assert result_codes.rows == 5

    result_cal = hist_sync.sync_meta_calendar(
        market="SH",
        settings=five_year_settings,
        warehouse=wh,
        adapter=fake,
    )
    assert result_cal.success
    assert (wh.meta_dir() / "codes.parquet").exists()
    assert (wh.meta_dir() / "calendar.parquet").exists()

    # Verify queryable
    wh.refresh_views()
    df_codes = wh.query_codes()
    assert len(df_codes) == 5
    df_cal = wh.query_calendar(market="SH")
    assert len(df_cal) > 0


def test_backfill_handles_partial_failures(five_year_settings):
    """Verify that the backfill continues past per-stock failures."""
    codes = ["000001.SZ", "600000.SH", "300750.SZ"]
    fake = MagicMock()
    fake.get_code_list.return_value = codes

    def _kline(codes, begin_date, end_date, period, **kwargs):
        code = codes.strip()
        if code == "600000.SH":
            raise RuntimeError("simulated SDK error")
        return _make_synthetic_kline(code, begin_date, end_date, period).assign(code=code)

    fake.get_kline.side_effect = _kline
    wh = hist_warehouse.get_warehouse(five_year_settings)

    result = hist_sync.sync_kline_daily(
        from_date=20240101,
        to_date=20241231,
        codes=codes,
        settings=five_year_settings,
        warehouse=wh,
        adapter=fake,
    )
    assert result.succeeded == 2
    assert result.failed == 1
    assert not result.success
    # Two of the three flat files should be written
    files = list((wh.root / "A_share" / "daily").glob("*.parquet"))
    assert len(files) == 2


def test_backfill_writes_period_metadata(five_year_settings):
    """A single sync run should refresh the per-period ``_metadata.json`` file."""
    codes = ["000001.SZ"]
    fake = FakeSDK(codes, year_start=2021, year_end=2025)
    wh = hist_warehouse.get_warehouse(five_year_settings)

    result = hist_sync.sync_kline_daily(
        from_date=20210101,
        to_date=20251231,
        codes=codes,
        settings=five_year_settings,
        warehouse=wh,
        adapter=fake,
    )
    assert result.success
    meta_path = wh.root / "A_share" / "daily" / "_metadata.json"
    assert meta_path.exists()
    payload = json.loads(meta_path.read_text())
    assert payload["file_count"] == 1
    assert payload["first_date"] == 20210101
    assert payload["last_date"] == 20251231
    assert "year" not in payload  # flat layout: no per-year field


def test_backfill_resumable_after_partial_run(five_year_settings):
    """Verify that re-running a sync overwrites existing files cleanly."""
    codes = ["000001.SZ"]
    fake = FakeSDK(codes, year_start=2024, year_end=2024)
    wh = hist_warehouse.get_warehouse(five_year_settings)

    result1 = hist_sync.sync_kline_daily(
        from_date=20240101,
        to_date=20241231,
        codes=codes,
        settings=five_year_settings,
        warehouse=wh,
        adapter=fake,
    )
    rows_first = result1.rows
    assert rows_first > 0

    # Re-run the same window
    result2 = hist_sync.sync_kline_daily(
        from_date=20240101,
        to_date=20241231,
        codes=codes,
        settings=five_year_settings,
        warehouse=wh,
        adapter=fake,
    )
    assert result2.success
    assert result2.rows == rows_first  # idempotent
    assert result2.succeeded == 1


def test_backfill_is_synced_checks(five_year_settings):
    """is_synced should flip from False to True after a successful backfill."""
    codes = ["000001.SZ", "600000.SH"]
    fake = FakeSDK(codes, year_start=2021, year_end=2025)
    wh = hist_warehouse.get_warehouse(five_year_settings)

    # Empty warehouse
    assert not wh.is_synced(20210101, 20251231, "day", codes)

    # Partial backfill (only 2024)
    hist_sync.sync_kline_daily(
        from_date=20240101,
        to_date=20241231,
        codes=codes,
        settings=five_year_settings,
        warehouse=wh,
        adapter=fake,
    )
    wh.refresh_views()
    # The flat file for 2024 doesn't cover 5 years
    assert wh.is_synced(20240101, 20241231, "day", codes)
    assert not wh.is_synced(20210101, 20251231, "day", codes)

    # Full backfill
    hist_sync.sync_kline_daily(
        from_date=20210101,
        to_date=20251231,
        codes=codes,
        settings=five_year_settings,
        warehouse=wh,
        adapter=fake,
    )
    wh.refresh_views()
    assert wh.is_synced(20210101, 20251231, "day", codes)


def test_warehouse_stats_after_backfill(five_year_settings):
    """Verify warehouse.stats() reports accurate numbers after backfill."""
    codes = ["000001.SZ", "600000.SH", "300750.SZ"]
    fake = FakeSDK(codes, year_start=2021, year_end=2025)
    wh = hist_warehouse.get_warehouse(five_year_settings)

    hist_sync.sync_kline_daily(
        from_date=20210101,
        to_date=20251231,
        codes=codes,
        settings=five_year_settings,
        warehouse=wh,
        adapter=fake,
    )

    stats = wh.stats()
    # Flat layout: 3 codes × 1 file per code = 3 files
    assert stats["periods"]["daily"]["file_count"] == 3
    assert stats["periods"]["daily"]["total_bytes"] > 0
    assert "year_count" not in stats["periods"]["daily"]
    assert stats["periods"]["daily"]["first_date"] == 20210101
    assert stats["periods"]["daily"]["last_date"] == 20251231
    assert stats["periods"]["weekly"]["file_count"] == 0
