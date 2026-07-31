"""PostgreSQL repository integration tests.

Set ``TEST_POSTGRES_URL`` to run locally. CI can point this at an ephemeral
PostgreSQL service; the test is skipped when no database is configured.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pandas as pd
import pytest

from adshare.core.config import Settings
from adshare.historical.warehouse import HistoricalWarehouse
from amazingdata.batch import sync_adjustment_factors, sync_kline_daily


pytestmark = pytest.mark.integration


@pytest.fixture()
def postgres_warehouse(monkeypatch):
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_AUTO_MIGRATE", "true")
    warehouse = HistoricalWarehouse(Settings())
    with warehouse._pool.connection() as conn:
        conn.execute("TRUNCATE market.sync_job, market.reference_data")
        conn.execute(
            "TRUNCATE market.adjustment_factor, market.daily_bar, market.weekly_bar, "
            "market.monthly_bar, market.trade_calendar, master.stock "
            "RESTART IDENTITY CASCADE"
        )
        conn.commit()
    try:
        yield warehouse
    finally:
        warehouse.close()


def test_round_trip_and_idempotent_upsert(postgres_warehouse):
    warehouse = postgres_warehouse
    codes = pd.DataFrame(
        {
            "code": ["000001.SZ"],
            "name": ["平安银行"],
            "comp_name": ["平安银行股份有限公司"],
            "list_date": [19910403],
            "delist_date": [0],
            "is_listed": [True],
            "board": ["主板"],
            "list_plate": ["主板"],
            "industry": ["银行"],
        }
    )
    warehouse.upsert_stocks(codes)

    bars = pd.DataFrame(
        {
            "date": [20260724],
            "open": [11.1],
            "high": [11.5],
            "low": [11.0],
            "close": [11.4],
            "volume": [1000],
            "amount": [11400.0],
        }
    )
    assert warehouse.upsert_kline("000001.SZ", "day", bars) == 1
    assert warehouse.upsert_adjustment_factors(
        "000001.SZ",
        pd.DataFrame(
            {
                "date": [20260701, 20260724],
                "adj_factor": [84.5, 85.25],
            }
        ),
    ) == 2
    bars.loc[0, "close"] = 11.45
    assert warehouse.upsert_kline("000001.SZ", "day", bars) == 1

    result = warehouse.query_kline(
        ["000001.SZ"], 20260724, 20260724, "day"
    )
    assert len(result) == 1
    assert result.iloc[0]["code"] == "000001.SZ"
    assert result.iloc[0]["name"] == "平安银行"
    assert result.iloc[0]["close"] == pytest.approx(11.45)
    assert result.iloc[0]["adj_factor"] == pytest.approx(85.25)

    codes.loc[0, "name"] = "平安银行新"
    warehouse.upsert_stocks(codes)
    renamed = warehouse.query_kline(
        ["000001.SZ"], 20260724, 20260724, "day"
    )
    assert renamed.iloc[0]["name"] == "平安银行新"
    assert warehouse.max_trade_date("day") == 20260724


def test_factor_timeline_reconcile_preserves_original_created_at(
    postgres_warehouse,
):
    warehouse = postgres_warehouse
    initial = pd.DataFrame(
        {
            "date": [20260701, 20260710, 20260723],
            "adj_factor": [84.5, 85.0, 85.25],
        }
    )
    assert (
        warehouse.replace_adjustment_factor_timeline("000001.SZ", initial)
        == 3
    )

    with warehouse._pool.connection() as conn:
        conn.execute(
            """
            UPDATE market.adjustment_factor
               SET created_at = TIMESTAMPTZ '2026-01-01 00:00:00+08',
                   source_updated_at = TIMESTAMPTZ '2026-01-01 00:00:00+08',
                   updated_at = TIMESTAMPTZ '2026-01-01 00:00:00+08'
            """
        )
        conn.commit()

    replacement = pd.DataFrame(
        {
            "date": [20260701, 20260723, 20260724],
            "adj_factor": [84.5, 85.5, 85.5],
        }
    )
    assert (
        warehouse.replace_adjustment_factor_timeline(
            "000001.SZ", replacement
        )
        == 3
    )

    rows = warehouse.execute_sql(
        """
        SELECT effective_date, adj_factor,
               created_at = TIMESTAMPTZ '2026-01-01 00:00:00+08'
                   AS kept_created_at,
               source_updated_at = TIMESTAMPTZ '2026-01-01 00:00:00+08'
                   AS kept_source_updated_at
          FROM market.adjustment_factor
         ORDER BY effective_date
        """
    )
    assert rows["effective_date"].astype(str).tolist() == [
        "2026-07-01",
        "2026-07-23",
        "2026-07-24",
    ]
    assert rows["adj_factor"].astype(float).tolist() == [84.5, 85.5, 85.5]
    assert rows["kept_created_at"].tolist() == [True, True, False]
    assert rows["kept_source_updated_at"].tolist() == [True, False, False]


def test_calendar_reference_and_read_only_sql(postgres_warehouse):
    warehouse = postgres_warehouse
    warehouse.upsert_calendar(
        pd.DataFrame(
            {
                "date": [20260724],
                "market": ["SH"],
                "is_trading_day": [True],
                "weekday": [4],
            }
        )
    )
    assert warehouse.query_calendar("SH").iloc[0]["date"] == 20260724

    reference = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "end_date": [20260630],
            "holder_num": [12345],
        }
    )
    warehouse.upsert_reference("shareholder", reference)
    reference.loc[0, "holder_num"] = 12000
    warehouse.upsert_reference("shareholder", reference)
    result = warehouse.query_shareholder("000001.SZ")
    assert len(result) == 1
    assert result.iloc[0]["holder_num"] == 12000

    assert len(warehouse.execute_sql("SELECT * FROM v_kline_day")) == 0
    with pytest.raises(ValueError):
        warehouse.execute_sql("DELETE FROM market.daily_bar")


def test_weekly_and_monthly_bars_stored_under_period_end_date(postgres_warehouse):
    """SDK labels week/month bars with the period's first trading day; the
    warehouse must store them under the period's last trading day."""
    warehouse = postgres_warehouse
    warehouse.upsert_calendar(
        pd.DataFrame(
            {
                "date": [
                    20260727, 20260728, 20260729, 20260730, 20260731,
                ],
                "market": ["SH"] * 5,
                "is_trading_day": [True] * 5,
                "weekday": [0, 1, 2, 3, 4],
            }
        )
    )

    weekly = pd.DataFrame(
        {
            "date": [20260727],  # week labelled with Monday by the SDK
            "open": [11.1],
            "high": [11.5],
            "low": [11.0],
            "close": [11.4],
            "volume": [1000],
            "amount": [11400.0],
        }
    )
    assert warehouse.upsert_kline("000001.SZ", "week", weekly) == 1
    rows = warehouse.query_kline(["000001.SZ"], 20260727, 20260731, "week")
    assert rows["date"].tolist() == [20260731]
    assert warehouse.max_trade_date("week") == 20260731

    monthly = weekly.copy()
    monthly["date"] = [20260701]  # month labelled with its first trading day
    assert warehouse.upsert_kline("000001.SZ", "month", monthly) == 1
    rows = warehouse.query_kline(["000001.SZ"], 20260701, 20260731, "month")
    assert rows["date"].tolist() == [20260731]
    assert warehouse.max_trade_date("month") == 20260731


def test_amazingdata_batch_writes_directly_to_postgres(postgres_warehouse):
    warehouse = postgres_warehouse

    class Adapter:
        def get_kline(self, **kwargs):
            assert kwargs["codes"] == "000001.SZ,600000.SH"
            return pd.DataFrame(
                {
                    "code": ["000001.SZ", "600000.SH"],
                    "date": [20260724, 20260724],
                    "open": [11.1, 10.0],
                    "high": [11.5, 10.4],
                    "low": [11.0, 9.9],
                    "close": [11.4, 10.2],
                    "volume": [1000, 2000],
                    "amount": [11400.0, 20400.0],
                }
            )

    settings = SimpleNamespace(
        sync_retry_attempts=1,
        sync_workers=1,
        max_codes_per_query=50,
    )
    result = sync_kline_daily(
        from_date=20260724,
        to_date=20260724,
        codes=["000001.SZ", "600000.SH"],
        batch_size=2,
        settings=settings,
        warehouse=warehouse,
        adapter=Adapter(),
    )

    assert result.success is True
    assert result.rows == 2
    assert len(
        warehouse.query_kline(
            ["000001.SZ", "600000.SH"], 20260724, 20260724, "day"
        )
    ) == 2
    jobs = warehouse.execute_sql(
        "SELECT job_status, records_inserted FROM market.sync_job"
    )
    assert jobs.iloc[0]["job_status"] == "SUCCESS"
    assert jobs.iloc[0]["records_inserted"] == 2


def test_adjustment_factors_are_stored_once_and_joined_to_bars(
    postgres_warehouse,
):
    warehouse = postgres_warehouse
    warehouse.upsert_kline(
        "000001.SZ",
        "week",
        pd.DataFrame(
            {
                "date": [20260724],
                "open": [11.1],
                "high": [11.5],
                "low": [11.0],
                "close": [11.4],
                "volume": [1000],
                "amount": [11400.0],
            }
        ),
    )

    class Adapter:
        def get_adjustment_factors(self, **kwargs):
            return pd.DataFrame(
                {
                    "code": ["000001.SZ"] * 4,
                    "date": [20260701, 20260702, 20260723, 20260724],
                    "adj_factor": [84.5, 84.5, 85.25, 85.25],
                }
            )

    settings = SimpleNamespace(
        max_codes_per_query=50,
        amazingdata_local_path="/tmp/amazingdata-factor-test",
    )
    result = sync_adjustment_factors(
        from_date=20260701,
        to_date=20260724,
        codes=["000001.SZ"],
        settings=settings,
        warehouse=warehouse,
        adapter=Adapter(),
    )

    assert result.success is True
    assert result.succeeded == 1
    assert result.rows == 2
    rows = warehouse.execute_sql(
        "SELECT effective_date, adj_factor FROM adjustment_factor "
        "ORDER BY effective_date"
    )
    assert len(rows) == 2
    assert rows["effective_date"].astype(str).tolist() == [
        "2026-07-01",
        "2026-07-23",
    ]
    weekly = warehouse.query_kline(
        ["000001.SZ"], 20260724, 20260724, "week"
    )
    assert weekly.iloc[0]["adj_factor"] == pytest.approx(85.25)
