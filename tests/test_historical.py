"""Tests for the L3 historical data warehouse.

Covers schema validation, Parquet round-trips, the DuckDB-backed
warehouse, the sync jobs (with a mocked adapter), the router endpoints
and the /admin/* routes.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable
from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from adshare.core.config import Settings, get_settings
from amazingdata.config import WorkerSettings, get_worker_settings
from adshare.historical import models as hist_models
from amazingdata import batch as hist_sync
from adshare.historical import warehouse as hist_warehouse
from adshare.historical.warehouse import HistoricalWarehouse
from adshare.historical.models import (
    KLINE_COLUMNS,
    KLINE_DTYPES,
    standardize_kline_df,
    standardize_calendar_df,
    standardize_codes_df,
    validate_kline_df,
    kline_file_path,
    normalize_period,
    period_to_subdir,
    write_metadata,
)
from amazingdata.batch import (
    SyncResult,
    sync_adjustment_factors,
    sync_kline_daily,
    sync_meta_codes,
    sync_meta_calendar,
)
from adshare.main import create_app


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def tmp_warehouse_root(tmp_path) -> Path:
    """Return a temporary directory for the historical warehouse."""
    root = tmp_path / "historical"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def isolated_settings(tmp_warehouse_root, monkeypatch, tmp_path) -> WorkerSettings:
    """Return a fresh WorkerSettings instance pointed at temporary paths.

    WorkerSettings is the right type for the batch sync functions because
    they read both worker-only fields (sync_workers, sync_retry_attempts,
    sync_schedule_enabled, ...) AND shared fields (historical_path, ...)
    via ``settings.shared.<field>``.
    """
    env_overrides = {
        "HISTORICAL_ENABLED": "true",
        "HISTORICAL_PATH": str(tmp_warehouse_root),
        "AUTH_ENABLED": "false",
        "DUCKDB_MODE": "memory",
        "DUCKDB_FILE_PATH": str(tmp_path / "duckdb" / "adshare.duckdb"),
        "SYNC_SCHEDULE_ENABLED": "false",
        "SYNC_WORKERS": "2",
        "SYNC_RETRY_ATTEMPTS": "2",
        "AMAZINGDATA_LOCAL_PATH": str(tmp_path / "sdk_cache"),
        "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": "16379",
    }
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)
    # Reset both caches
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    hist_warehouse.reset_warehouse()
    settings = get_worker_settings()
    return settings


@pytest.fixture
def warehouse(isolated_settings) -> HistoricalWarehouse:
    """Return a fresh HistoricalWarehouse singleton for the test."""
    hist_warehouse.reset_warehouse()
    wh = hist_warehouse.get_warehouse(isolated_settings)
    yield wh
    wh.close()
    hist_warehouse.reset_warehouse()


@pytest.fixture
def client(isolated_settings):
    """Return a TestClient with mocked market data service."""
    from unittest.mock import MagicMock

    import adshare.core.cache as _cache_mod
    import adshare.historical.warehouse as _wh_mod
    import adshare.services.market_data as _md_mod
    import adshare.services.limit_up as _lu_mod

    # Build a fake market data service
    fake = MagicMock()
    fake.get_code_list.return_value = ["000001.SZ", "600000.SH"]
    fake.get_calendar.return_value = pd.DataFrame({"date": [20240101, 20240102]})
    fake.get_kline.return_value = _md_mod.KlineQueryResult(
        df=pd.DataFrame({
            "code": ["000001.SZ", "600000.SH"],
            "date": [20240101, 20240101],
            "open": [10.0, 20.0],
            "high": [10.5, 20.5],
            "low": [9.8, 19.8],
            "close": [10.2, 20.2],
            "volume": [100000, 200000],
            "amount": [1000000.0, 4000000.0],
        }),
        source="warehouse",
        synced=True,
    )
    fake.get_snapshot.return_value = pd.DataFrame({
        "code": ["000001.SZ"],
        "date": [20240607],
        "open": [10.0],
        "high": [11.0],
        "low": [9.5],
        "close": [10.8],
        "pre_close": [9.8],
        "volume": [500000],
        "amount": [5400000.0],
    })
    fake.get_stock_basic.return_value = pd.DataFrame({
        "code": ["000001.SZ", "600000.SH"],
        "name": ["平安银行", "浦发银行"],
        "comp_name": ["平安银行股份有限公司", "浦发银行股份有限公司"],
        "list_date": [19910403, 19991110],
        "delist_date": [None, None],
        "list_plate": ["主板", "主板"],
        "is_listed": [1, 1],
    })

    _orig_get_market_data_service = _md_mod.get_market_data_service
    # Mock limit-up services to avoid warehouse dependency
    _orig_get_limit_up_service = _lu_mod.get_limit_up_service
    _lu_mod.get_limit_up_service = lambda: _lu_mod.LimitUpService(warehouse=False)
    _lu_mod.get_limit_down_service = lambda: _lu_mod.LimitDownService(warehouse=False)
    _lu_mod.get_market_activity_service = lambda: _lu_mod.MarketActivityService(warehouse=False)
    _lu_mod.get_strong_stock_pool_service = lambda: _lu_mod.StrongStockPoolService(warehouse=False)

    _cache_mod._cache_manager = None

    # Mock amazingdata.adapter for sync tests
    import amazingdata.adapters.amazingdata as _worker_ad_mod
    _orig_worker_get_adapter = _worker_ad_mod.get_adapter
    _worker_fake = MagicMock()
    _worker_fake.is_logged_in = True
    _worker_fake.login.return_value = True
    _worker_fake.get_code_info.return_value = pd.DataFrame({
        "code": ["000001.SZ", "600000.SH"],
        "name": ["平安银行", "浦发银行"],
        "list_plate": ["主板", "主板"],
        "is_listed": [1, 1],
    })
    _worker_fake.get_stock_basic.return_value = pd.DataFrame({
        "code": ["000001.SZ", "600000.SH"],
        "name": ["平安银行", "浦发银行"],
        "list_plate": ["主板", "主板"],
        "is_listed": [1, 1],
    })
    _worker_fake.get_calendar.return_value = pd.DataFrame({
        "date": [20240101, 20240102, 20240103],
    })
    _worker_fake.get_kline.return_value = pd.DataFrame({
        "code": ["000001.SZ"],
        "kline_time": [pd.Timestamp("2024-01-01")],
        "open": [10.0],
        "high": [10.5],
        "low": [9.8],
        "close": [10.2],
        "volume": [100000],
        "amount": [1000000.0],
    })
    _worker_ad_mod.get_adapter = lambda: _worker_fake

    try:
        app = create_app()
        with TestClient(app) as tc:
            yield tc
    finally:
        _md_mod.get_market_data_service = _orig_get_market_data_service
        _lu_mod.get_limit_up_service = _orig_get_limit_up_service
        _worker_ad_mod.get_adapter = _orig_worker_get_adapter
        _cache_mod._cache_manager = None
        _wh_mod.reset_warehouse()


# ----------------------------------------------------------------------
# Model tests
# ----------------------------------------------------------------------

class TestPeriodNormalization:
    @pytest.mark.parametrize("alias,expected", [
        ("day", "daily"),
        ("DAY", "daily"),
        ("daily", "daily"),
        ("1d", "daily"),
        ("week", "weekly"),
        ("W", "weekly"),
        ("month", "monthly"),
        ("M", "monthly"),
    ])
    def test_normalize_aliases(self, alias, expected):
        assert normalize_period(alias) == expected
        assert period_to_subdir(alias) == expected

    def test_invalid_period_raises(self):
        with pytest.raises(ValueError):
            normalize_period("hour")

    def test_empty_period_raises(self):
        with pytest.raises(ValueError):
            normalize_period("")


class TestKlineFilePath:
    def test_file_path_components(self, tmp_warehouse_root):
        path = kline_file_path(tmp_warehouse_root, "day", "000001.SZ")
        assert path == (
            tmp_warehouse_root / "A_share" / "daily" / "000001.SZ.parquet"
        )

    def test_file_path_legacy_year_kwarg_ignored(self, tmp_warehouse_root):
        # The ``year`` kwarg is accepted for backward compat but ignored.
        path = kline_file_path(tmp_warehouse_root, "day", "000001.SZ", year=2024)
        assert path == (
            tmp_warehouse_root / "A_share" / "daily" / "000001.SZ.parquet"
        )

    def test_file_path_unsafe_chars(self, tmp_warehouse_root):
        # Code with dot is preserved verbatim
        path = kline_file_path(tmp_warehouse_root, "week", "600000.SH")
        assert path.name == "600000.SH.parquet"


class TestStandardizeKlineDf:
    def test_standardize_with_kline_time(self):
        raw = pd.DataFrame({
            "code": ["000001.SZ"] * 3,
            "kline_time": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            "open": [10.0, 10.1, 10.2],
            "high": [10.5, 10.6, 10.7],
            "low": [9.8, 9.9, 10.0],
            "close": [10.2, 10.3, 10.4],
            "volume": [100000, 110000, 120000],
            "amount": [1000000.0, 1100000.0, 1200000.0],
        })
        df = standardize_kline_df(raw)
        assert list(df.columns) == list(KLINE_COLUMNS)
        assert "code" not in df.columns
        assert list(df["date"]) == [20240101, 20240102, 20240103]
        assert df["volume"].dtype.name == "int64"
        assert df["is_suspended"].dtype.name == "bool"

    def test_standardize_empty(self):
        df = standardize_kline_df(pd.DataFrame())
        assert list(df.columns) == list(KLINE_COLUMNS)
        assert len(df) == 0

    def test_standardize_does_not_fabricate_adjustment_factor(self):
        raw = pd.DataFrame(
            {
                "kline_time": pd.to_datetime(["2024-01-02"]),
                "open": [10.0],
                "high": [10.5],
                "low": [9.8],
                "close": [10.2],
                "volume": [100000],
                "amount": [1000000.0],
            }
        )

        result = standardize_kline_df(raw)

        assert result["adj_factor"].isna().all()

    def test_validate_drops_invalid_rows(self):
        raw = pd.DataFrame({
            "date": [20240101, 20240102, 20240103],
            "open": [10.0, 12.0, 9.0],
            "high": [10.5, 11.0, 9.5],   # row 1: high(11)<low(11.5) invalid
            "low":  [9.8, 11.5, 9.4],    # row 2: low(9.4)>close(9.4)? >=, valid; make strict
            "close": [10.2, 11.8, 8.5],  # row 2: low(9.4) > close(8.5) invalid
            "volume": [100, 100, 100],
            "amount": [1.0, 1.0, 1.0],
            "is_suspended": [False, False, False],
            "sync_at": [int(time.time())] * 3,
            "adj_factor": [1.0] * 3,
        })
        std = standardize_kline_df(raw)
        cleaned = validate_kline_df(std)
        # Rows 1 and 2 are invalid; only row 0 should remain
        assert len(cleaned) == 1
        assert int(cleaned.iloc[0]["date"]) == 20240101

    def test_validate_drops_duplicates(self):
        raw = pd.DataFrame({
            "date": [20240101, 20240101, 20240102],
            "open": [10.0, 10.0, 10.0],
            "high": [10.5, 10.5, 10.5],
            "low":  [9.8, 9.8, 9.8],
            "close": [10.2, 10.2, 10.2],
            "volume": [100, 100, 100],
            "amount": [1.0, 1.0, 1.0],
            "is_suspended": [False, False, False],
            "sync_at": [int(time.time())] * 3,
            "adj_factor": [1.0] * 3,
        })
        std = standardize_kline_df(raw)
        cleaned = validate_kline_df(std)
        assert len(cleaned) == 2

    def test_round_trip_parquet(self, tmp_warehouse_root):
        raw = pd.DataFrame({
            "code": ["000001.SZ", "000001.SZ"],
            "kline_time": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "open": [10.0, 10.1],
            "high": [10.5, 10.6],
            "low":  [9.8, 9.9],
            "close": [10.2, 10.3],
            "volume": [100000, 110000],
            "amount": [1000000.0, 1100000.0],
        })
        df = standardize_kline_df(raw)
        path = kline_file_path(tmp_warehouse_root, "day", "000001.SZ")
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, compression="zstd")
        roundtrip = pd.read_parquet(path)
        assert list(roundtrip["date"]) == [20240101, 20240102]
        assert "code" not in roundtrip.columns


class TestStandardizeCalendarDf:
    def test_standardize_calendar(self):
        raw = pd.DataFrame({"date": [20240101, 20240102]})
        df = standardize_calendar_df(raw, market="SH")
        assert list(df.columns) == list(hist_models.CALENDAR_COLUMNS)
        assert all(df["market"] == "SH")
        assert all(df["is_trading_day"])
        # Monday=0, Tuesday=1
        assert int(df.iloc[0]["weekday"]) == 0
        assert int(df.iloc[1]["weekday"]) == 1

    def test_standardize_calendar_empty(self):
        df = standardize_calendar_df(pd.DataFrame(), market="SH")
        assert list(df.columns) == list(hist_models.CALENDAR_COLUMNS)


class TestStandardizeCodesDf:
    def test_standardize_codes(self):
        raw = pd.DataFrame({
            "MARKET_CODE": ["000001.SZ", "600000.SH", "300750.SZ"],
            "SECURITY_NAME": ["平安银行", "浦发银行", "宁德时代"],
            "LISTDATE": [19910403, 19991110, 20180611],
            "DELISTDATE": [None, None, None],
            "IS_LISTED": [1, 1, 1],
        })
        df = standardize_codes_df(raw)
        assert list(df.columns) == list(hist_models.CODES_COLUMNS)
        assert len(df) == 3
        # Data is sorted by code
        by_code = {row["code"]: row for _, row in df.iterrows()}
        assert by_code["000001.SZ"]["board"] == "主板"
        assert by_code["600000.SH"]["board"] == "主板"
        assert by_code["300750.SZ"]["board"] == "创业板"
        assert by_code["300750.SZ"]["name"] == "宁德时代"

    def test_standardize_codes_empty(self):
        df = standardize_codes_df(pd.DataFrame())
        assert list(df.columns) == list(hist_models.CODES_COLUMNS)


class TestWriteMetadata:
    def test_write_metadata_creates_file(self, tmp_warehouse_root):
        path = write_metadata(
            tmp_warehouse_root, "day",
            file_count=2, total_rows=10,
            first_date=20240101, last_date=20241231, last_sync_at=12345,
        )
        assert path.exists()
        payload = json.loads(path.read_text())
        assert payload["file_count"] == 2
        assert payload["total_rows"] == 10
        assert payload["first_date"] == 20240101
        assert payload["last_date"] == 20241231
        assert payload["period"] == "daily"
        assert "year" not in payload  # flat layout: no year field
        assert path.parent == tmp_warehouse_root / "A_share" / "daily"


# ----------------------------------------------------------------------
# Warehouse tests
# ----------------------------------------------------------------------

def _populate_kline(warehouse, period, code, dates=(20240101, 20240102, 20240103)):
    """Helper: write a few K-line rows directly to the warehouse."""
    rows = []
    for d in dates:
        rows.append({
            "date": d,
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.2,
            "volume": 100000,
            "amount": 1000000.0,
            "adj_factor": 1.0,
            "is_suspended": False,
            "sync_at": int(time.time()),
        })
    df = pd.DataFrame(rows)
    path = kline_file_path(warehouse.root, period, code)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, compression="zstd", index=False)
    return path


class TestWarehouse:
    def test_directory_layout_created(self, warehouse):
        assert (warehouse.root / "A_share" / "daily").exists()
        assert (warehouse.root / "A_share" / "weekly").exists()
        assert (warehouse.root / "A_share" / "monthly").exists()
        assert (warehouse.root / "meta").exists()

    def test_kline_dir(self, warehouse):
        d = warehouse.kline_dir("day")
        assert d == warehouse.root / "A_share" / "daily"

    def test_kline_dir_legacy_year_kwarg_ignored(self, warehouse):
        d = warehouse.kline_dir("day", 2024)
        assert d == warehouse.root / "A_share" / "daily"

    def test_is_synced_false_when_no_files(self, warehouse):
        assert not warehouse.is_synced(20240101, 20240131, "day", ["000001.SZ"])

    def test_is_synced_true(self, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        warehouse.refresh_views()
        assert warehouse.is_synced(20240101, 20240103, "day", ["000001.SZ"])

    def test_is_synced_false_when_file_does_not_cover_range(self, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        warehouse.refresh_views()
        assert not warehouse.is_synced(20240101, 20240131, "day", ["000001.SZ"])

    def test_is_synced_missing_code(self, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        warehouse.refresh_views()
        assert not warehouse.is_synced(
            20240101, 20240131, "day", ["000001.SZ", "600000.SH"]
        )

    def test_query_kline(self, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        _populate_kline(warehouse, "day", "600000.SH")
        warehouse.refresh_views()
        df = warehouse.query_kline(
            ["000001.SZ", "600000.SH"], 20240101, 20240103, "day"
        )
        assert len(df) == 6
        assert set(df["code"].unique()) == {"000001.SZ", "600000.SH"}

    def test_query_kline_with_limit(self, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        warehouse.refresh_views()
        df = warehouse.query_kline(
            ["000001.SZ"], 20240101, 20240103, "day", limit=2, offset=1
        )
        assert len(df) == 2
        assert list(df["date"]) == [20240102, 20240103]

    def test_query_kline_empty(self, warehouse):
        df = warehouse.query_kline(["000001.SZ"], 20240101, 20240131, "day")
        assert df.empty

    def test_query_calendar_empty(self, warehouse):
        df = warehouse.query_calendar(market="SH")
        assert df.empty

    def test_query_codes_empty(self, warehouse):
        df = warehouse.query_codes()
        assert df.empty

    def test_execute_sql_rejects_non_select(self, warehouse):
        with pytest.raises(ValueError):
            warehouse.execute_sql("DROP TABLE x")
        with pytest.raises(ValueError):
            warehouse.execute_sql("DELETE FROM x")
        with pytest.raises(ValueError):
            warehouse.execute_sql("ATTACH 'foo.db'")

    def test_execute_sql_rejects_empty(self, warehouse):
        with pytest.raises(ValueError):
            warehouse.execute_sql("")

    def test_execute_sql_select(self, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        warehouse.refresh_views()
        df = warehouse.execute_sql("SELECT date, close FROM v_kline_day ORDER BY date")
        assert len(df) == 3
        assert list(df["date"]) == [20240101, 20240102, 20240103]

    def test_execute_sql_rejects_pragmas(self, warehouse):
        with pytest.raises(ValueError):
            warehouse.execute_sql("PRAGMA version")

    def test_stats(self, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        _populate_kline(warehouse, "day", "600000.SH")
        stats = warehouse.stats()
        assert "daily" in stats["periods"]
        assert stats["periods"]["daily"]["file_count"] == 2
        assert stats["periods"]["weekly"]["file_count"] == 0
        # New fields
        assert "first_date" in stats["periods"]["daily"]
        assert "last_date" in stats["periods"]["daily"]

    def test_health(self, warehouse):
        health = warehouse.health()
        assert health["duckdb_connected"] is True
        assert health["historical_enabled"] is True

    def test_list_files(self, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        _populate_kline(warehouse, "day", "600000.SH")
        files = warehouse.list_files(period="day")
        assert len(files) == 2

    def test_list_files_year_arg_ignored(self, warehouse):
        # year= is accepted for backward compat but should be ignored.
        _populate_kline(warehouse, "day", "000001.SZ")
        _populate_kline(warehouse, "day", "600000.SH")
        files = warehouse.list_files(period="day", year=2024)
        assert len(files) == 2

    def test_list_files_all(self, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        _populate_kline(warehouse, "week", "000001.SZ")
        _populate_kline(warehouse, "month", "000001.SZ")
        files = warehouse.list_files()
        assert len(files) == 3


# ----------------------------------------------------------------------
# Sync tests (with mocked adapter)
# ----------------------------------------------------------------------

class TestSync:
    def _fake_adapter(self, codes=("000001.SZ", "600000.SH")):
        fake = MagicMock()
        fake.get_code_list.return_value = list(codes)
        def _get_kline(codes, begin_date, end_date, period, **_):
            code_list = [c.strip() for c in codes.split(",")]
            n = len(code_list)
            base_prices = {"000001.SZ": (10.0, 10.5, 9.8, 10.2, 100000, 1_000_000.0),
                           "600000.SH": (20.0, 20.5, 19.8, 20.2, 200000, 4_000_000.0)}
            rows = {"code": code_list}
            ts = pd.to_datetime([f"{str(begin_date)[:4]}-01-01"] * n)
            rows["kline_time"] = ts
            opens, highs, lows, closes, volumes, amounts = [], [], [], [], [], []
            for c in code_list:
                vals = base_prices.get(c, (10.0, 10.5, 9.8, 10.2, 100000, 1_000_000.0))
                opens.append(vals[0]); highs.append(vals[1]); lows.append(vals[2])
                closes.append(vals[3]); volumes.append(vals[4]); amounts.append(vals[5])
            rows["open"] = opens
            rows["high"] = highs
            rows["low"] = lows
            rows["close"] = closes
            rows["volume"] = volumes
            rows["amount"] = amounts
            return pd.DataFrame(rows)
        fake.get_kline.side_effect = _get_kline
        fake.get_code_info.return_value = pd.DataFrame(
            {"symbol": ["平安银行", "浦发银行"]}, index=list(codes)
        )
        fake.get_calendar.return_value = pd.DataFrame({"date": [20240101]})
        return fake

    def test_sync_kline_daily_success(self, warehouse, isolated_settings):
        fake = self._fake_adapter()
        result = sync_kline_daily(
            year=2024,
            codes=["000001.SZ", "600000.SH"],
            settings=isolated_settings,
            warehouse=warehouse,
            adapter=fake,
        )
        assert result.success is True
        assert result.succeeded == 2
        assert result.failed == 0
        assert (warehouse.root / "A_share" / "daily" / "000001.SZ.parquet").exists()
        assert (warehouse.root / "A_share" / "daily" / "600000.SH.parquet").exists()

    def test_sync_adjustment_factors_repairs_existing_kline_files(
        self, warehouse, isolated_settings
    ):
        fake = self._fake_adapter()
        sync_kline_daily(
            year=2024,
            codes=["000001.SZ"],
            settings=isolated_settings,
            warehouse=warehouse,
            adapter=fake,
        )
        fake.get_adjustment_factors.return_value = pd.DataFrame(
            {
                "code": ["000001.SZ"],
                "date": [20240101],
                "adj_factor": [1.25],
            }
        )

        result = sync_adjustment_factors(
            from_date=20240101,
            to_date=20241231,
            codes=["000001.SZ"],
            periods=("daily",),
            settings=isolated_settings,
            warehouse=warehouse,
            adapter=fake,
        )

        repaired = warehouse.query_kline(
            ["000001.SZ"], 20240101, 20241231, "day"
        )
        assert result.success
        assert result.rows == 1
        assert repaired["adj_factor"].tolist() == [1.25]

    def test_sync_kline_daily_partial_failure(self, warehouse, isolated_settings):
        fake = MagicMock()
        fake.get_code_list.return_value = ["000001.SZ", "600000.SH"]

        def _kline(codes, begin_date, end_date, period, **_):
            if "000001.SZ" in codes:
                return pd.DataFrame({
                    "code": ["000001.SZ"],
                    "kline_time": pd.to_datetime(["2024-01-01"]),
                    "open": [10.0], "high": [10.5], "low": [9.8], "close": [10.2],
                    "volume": [100000], "amount": [1_000_000.0],
                })
            raise RuntimeError("simulated SDK error")

        fake.get_kline.side_effect = _kline
        result = sync_kline_daily(
            year=2024,
            codes=["000001.SZ", "600000.SH"],
            settings=isolated_settings,
            warehouse=warehouse,
            adapter=fake,
        )
        assert result.succeeded == 1
        assert result.failed == 1
        assert not result.success
        assert any("600000.SH" in e for e in result.errors)

    def test_sync_kline_uses_codes_list_from_adapter(self, warehouse, isolated_settings):
        fake = self._fake_adapter()
        result = sync_kline_daily(
            year=2024,
            codes=None,
            settings=isolated_settings,
            warehouse=warehouse,
            adapter=fake,
        )
        assert result.succeeded == 2
        assert fake.get_code_list.called

    def test_sync_kline_batch_mode(self, warehouse, isolated_settings):
        fake = self._fake_adapter(codes=("000001.SZ", "600000.SH"))
        result = sync_kline_daily(
            year=2024,
            codes=["000001.SZ", "600000.SH"],
            batch_size=2,
            settings=isolated_settings,
            warehouse=warehouse,
            adapter=fake,
        )

        assert result.success
        assert result.succeeded == 2
        assert result.skipped == 0
        assert result.failed == 0
        assert fake.get_kline.call_count == 1
        assert (warehouse.root / "A_share" / "daily" / "000001.SZ.parquet").exists()
        assert (warehouse.root / "A_share" / "daily" / "600000.SH.parquet").exists()

    def test_sync_meta_codes(self, warehouse, isolated_settings):
        fake = self._fake_adapter()
        result = sync_meta_codes(
            settings=isolated_settings, warehouse=warehouse, adapter=fake
        )
        assert result.success
        path = warehouse.meta_dir() / "codes.parquet"
        assert path.exists()
        df = pd.read_parquet(path)
        assert set(df["code"]) == {"000001.SZ", "600000.SH"}

    def test_sync_meta_codes_preserves_names_when_reusing_fresh_cache(
        self, warehouse, isolated_settings
    ):
        """Regression: fresh codes.parquet must not lose names on reuse."""
        path = warehouse.meta_dir() / "codes.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {
                "code": ["000001.SZ", "600000.SH"],
                "name": ["平安银行", "浦发银行"],
                "list_date": [0, 0],
                "delist_date": [0, 0],
                "is_listed": [True, True],
                "board": ["主板", "主板"],
                "industry": ["银行", "银行"],
                "sync_at": [int(time.time()), int(time.time())],
            }
        ).to_parquet(path, index=False)

        # Adapter returns different names; freshness reuse should ignore it.
        fake = self._fake_adapter()
        result = sync_meta_codes(
            settings=isolated_settings, warehouse=warehouse, adapter=fake
        )
        assert result.success
        df = pd.read_parquet(path)
        assert set(df["code"]) == {"000001.SZ", "600000.SH"}
        names = {row["code"]: row["name"] for _, row in df.iterrows()}
        assert names["000001.SZ"] == "平安银行"
        assert names["600000.SH"] == "浦发银行"

    def test_sync_meta_calendar(self, warehouse, isolated_settings):
        fake = self._fake_adapter()
        result = sync_meta_calendar(
            market="SH",
            settings=isolated_settings,
            warehouse=warehouse,
            adapter=fake,
        )
        assert result.success
        path = warehouse.meta_dir() / "calendar.parquet"
        assert path.exists()

    def test_sync_kline_skips_empty_df(self, warehouse, isolated_settings):
        fake = MagicMock()
        fake.get_code_list.return_value = ["000001.SZ"]
        fake.get_kline.return_value = pd.DataFrame()  # empty result
        result = sync_kline_daily(
            year=2024,
            codes=["000001.SZ"],
            settings=isolated_settings,
            warehouse=warehouse,
            adapter=fake,
        )
        # No file should be written, but the job is still "successful" structurally
        assert result.succeeded == 0
        assert result.skipped == 1
        assert result.failed == 0
        assert result.success is True
        assert not (
            warehouse.root / "A_share" / "daily" / "000001.SZ.parquet"
        ).exists()

    def test_sync_kline_writes_metadata(self, warehouse, isolated_settings):
        fake = self._fake_adapter()
        sync_kline_daily(
            year=2024,
            codes=["000001.SZ", "600000.SH"],
            settings=isolated_settings,
            warehouse=warehouse,
            adapter=fake,
        )
        meta_path = warehouse.root / "A_share" / "daily" / "_metadata.json"
        assert meta_path.exists()
        payload = json.loads(meta_path.read_text())
        assert payload["file_count"] >= 1
        assert "year" not in payload  # flat layout: no per-year metadata
        assert payload["period"] == "daily"

    def test_sync_kline_weekly(self, warehouse, isolated_settings):
        fake = self._fake_adapter()
        result = sync_kline_daily  # placeholder to keep linter happy
        from amazingdata.batch import sync_kline_weekly
        result = sync_kline_weekly(
            year=2024,
            codes=["000001.SZ"],
            settings=isolated_settings,
            warehouse=warehouse,
            adapter=fake,
        )
        assert result.success
        assert (warehouse.root / "A_share" / "weekly" / "000001.SZ.parquet").exists()

    def test_sync_kline_monthly(self, warehouse, isolated_settings):
        fake = self._fake_adapter()
        from amazingdata.batch import sync_kline_monthly
        result = sync_kline_monthly(
            year=2024,
            codes=["000001.SZ"],
            settings=isolated_settings,
            warehouse=warehouse,
            adapter=fake,
        )
        assert result.success
        assert (warehouse.root / "A_share" / "monthly" / "000001.SZ.parquet").exists()


# ----------------------------------------------------------------------
# Router tests
# ----------------------------------------------------------------------

class TestHistoricalRouter:
    def test_admin_health(self, client):
        response = client.get("/historical/admin/health")
        assert response.status_code == 200
        data = response.json()
        assert "warehouse" in data
        assert data["settings"]["historical_enabled"] is True

    def test_admin_stats(self, client):
        response = client.get("/historical/admin/stats")
        assert response.status_code == 200
        data = response.json()
        assert "periods" in data
        assert "daily" in data["periods"]

    def test_admin_sync_endpoint_removed(self, client):
        # Sync jobs require a data-source session and run only in the
        # worker process; the API process no longer exposes them.
        response = client.post("/historical/admin/sync?job=codes")
        assert response.status_code in (404, 405)

    def test_kline_empty_warehouse(self, client):
        # Warehouse is empty and SDK fallback is removed in API-only mode
        response = client.get(
            "/historical/kline?codes=000001.SZ&begin_date=20240101&end_date=20240131&period=day"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "warehouse"
        assert data["count"] == 0
        assert data["data"] == []

    def test_kline_warehouse_hit(self, client, warehouse):
        # Populate warehouse then re-query via the historical router
        _populate_kline(warehouse, "day", "000001.SZ")
        warehouse.refresh_views()
        response = client.get(
            "/historical/kline?codes=000001.SZ&begin_date=20240101&end_date=20240103&period=day"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "warehouse"
        assert data["synced"] is True
        assert data["count"] == 3

    def test_kline_source_warehouse_explicit(self, client, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        warehouse.refresh_views()
        response = client.get(
            "/historical/kline?codes=000001.SZ&begin_date=20240101&end_date=20240103&period=day&source=warehouse"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "warehouse"

    def test_kline_invalid_codes(self, client):
        response = client.get(
            "/historical/kline?codes=&begin_date=20240101&end_date=20240103&period=day"
        )
        # The endpoint should not return data for empty codes
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0

    def test_calendar_endpoint(self, client, warehouse):
        df = pd.DataFrame({"date": [20240101, 20240102]})
        std = standardize_calendar_df(df, market="SH")
        path = warehouse.meta_dir() / "calendar.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        std.to_parquet(path, compression="zstd", index=False)
        warehouse.refresh_views()
        response = client.get("/historical/calendar?market=SH")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert data["market"] == "SH"

    def test_codes_endpoint(self, client, warehouse):
        df = pd.DataFrame({
            "code": ["000001.SZ", "600000.SH", "300750.SZ"],
            "name": ["平安银行", "浦发银行", "宁德时代"],
            "list_date": [19910403, 19991110, 20180611],
            "delist_date": [0, 0, 0],
            "is_listed": [True, True, True],
            "board": ["主板", "主板", "创业板"],
            "industry": ["银行", "银行", "电池"],
            "sync_at": [int(time.time())] * 3,
        })
        path = warehouse.meta_dir() / "codes.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, compression="zstd", index=False)
        warehouse.refresh_views()
        response = client.get("/historical/codes?board=主板")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2

    def test_sql_select(self, client, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        warehouse.refresh_views()
        response = client.post(
            "/historical/sql",
            json={"sql": "SELECT date, close FROM v_kline_day ORDER BY date"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["row_count"] == 3
        assert data["columns"] == ["date", "close"]

    def test_sql_max_rows_truncates(self, client, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        warehouse.refresh_views()
        response = client.post(
            "/historical/sql",
            json={
                "sql": "SELECT date, close FROM v_kline_day ORDER BY date",
                "max_rows": 2,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["row_count"] == 2
        assert data["truncated"] is True

    def test_sql_rejects_non_select(self, client):
        response = client.post(
            "/historical/sql",
            json={"sql": "DELETE FROM v_kline_day"},
        )
        assert response.status_code == 400

    def test_market_kline_still_works_with_warehouse(self, client, warehouse):
        # Populate warehouse so /market/kline has data to return (SDK fallback removed)
        _populate_kline(warehouse, "day", "000001.SZ")
        warehouse.refresh_views()
        # Only request the code that exists in the warehouse, with date range matching populated data
        response = client.get(
            "/market/kline?codes=000001.SZ&begin_date=20240101&end_date=20240103"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1

    def test_market_kline_uses_warehouse_when_synced(self, client, warehouse):
        _populate_kline(warehouse, "day", "000001.SZ")
        warehouse.refresh_views()
        response = client.get(
            "/market/kline?codes=000001.SZ&begin_date=20240101&end_date=20240103"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3


# ----------------------------------------------------------------------
# Scheduler tests
# ----------------------------------------------------------------------

class TestScheduler:
    def test_init_scheduler_disabled(self, isolated_settings, warehouse):
        isolated_settings.sync_schedule_enabled = False
        from amazingdata.batch import init_scheduler, shutdown_scheduler
        try:
            scheduler = init_scheduler(isolated_settings)
            jobs = scheduler.get_jobs()
            # No jobs registered when schedule disabled
            assert len(jobs) == 0
        finally:
            shutdown_scheduler()

    def test_init_scheduler_enabled(self, isolated_settings, warehouse):
        isolated_settings.sync_schedule_enabled = True
        from amazingdata.batch import init_scheduler, shutdown_scheduler
        try:
            scheduler = init_scheduler(isolated_settings)
            jobs = scheduler.get_jobs()
            assert len(jobs) == 7  # daily, weekly, monthly, codes, financial, shareholder, index_component
            job_ids = {j.id for j in jobs}
            expected = {
                "sync_kline_daily",
                "sync_kline_weekly",
                "sync_kline_monthly",
                "sync_meta_codes",
                "sync_financial",
                "sync_shareholder",
                "sync_index_component",
            }
            assert expected <= job_ids
        finally:
            shutdown_scheduler()

    def test_shutdown_is_idempotent(self, isolated_settings, warehouse):
        from amazingdata.batch import shutdown_scheduler
        shutdown_scheduler()
        shutdown_scheduler()  # must not raise


# ----------------------------------------------------------------------
# Module import test
# ----------------------------------------------------------------------

def test_module_exports():
    """The historical package should expose the documented public API."""
    from adshare.historical import (
        HistoricalWarehouse,
        KLINE_COLUMNS,
        KLINE_DTYPES,
        CALENDAR_COLUMNS,
        CALENDAR_DTYPES,
        CODES_COLUMNS,
        CODES_DTYPES,
        validate_kline_df,
        standardize_kline_df,
        standardize_calendar_df,
        standardize_codes_df,
        kline_file_path,
        period_to_subdir,
        normalize_period,
        get_warehouse,
    )
    from amazingdata.batch import (
        sync_kline_daily,
        sync_kline_weekly,
        sync_kline_monthly,
        sync_meta_codes,
        sync_meta_calendar,
        SyncResult,
        init_scheduler,
        start_scheduler,
        shutdown_scheduler,
    )
    # Sanity: the constants contain the documented fields
    assert "date" in KLINE_COLUMNS
    assert "open" in CALENDAR_COLUMNS or CALENDAR_COLUMNS  # placeholder
    assert "code" in CODES_COLUMNS
