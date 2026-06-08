"""Tests for shared response mappers."""

from __future__ import annotations

import pandas as pd

from adshare.services.mappers import (
    dataframe_to_json_rows,
    dataframe_to_historical_kline_records,
    dataframe_to_kline_items,
    dataframe_to_snapshot_items,
)


def test_dataframe_to_kline_items_accepts_kline_time():
    df = pd.DataFrame(
        {
            "code": ["000001.SZ"],
            "kline_time": [pd.Timestamp("2024-01-02")],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "volume": [100000],
            "amount": [1_000_000.0],
        }
    )

    items = dataframe_to_kline_items(df)

    assert len(items) == 1
    assert items[0].code == "000001.SZ"
    assert items[0].date == 20240102


def test_dataframe_to_historical_kline_records_keeps_l3_fields():
    df = pd.DataFrame(
        {
            "code": ["000001.SZ"],
            "date": [20240102],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "volume": [100000],
            "amount": [1_000_000.0],
            "adj_factor": [1.0],
            "is_suspended": [False],
            "sync_at": [12345],
        }
    )

    records = dataframe_to_historical_kline_records(df)

    assert len(records) == 1
    assert records[0].code == "000001.SZ"
    assert records[0].adj_factor == 1.0
    assert records[0].is_suspended is False
    assert records[0].sync_at == 12345


def test_dataframe_to_snapshot_items_maps_optional_fields():
    df = pd.DataFrame(
        {
            "code": ["000001.SZ"],
            "date": [20240607],
            "time": [145900],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.8],
            "volume": [500000],
            "amount": [5_400_000.0],
        }
    )

    items = dataframe_to_snapshot_items(df)

    assert len(items) == 1
    assert items[0].code == "000001.SZ"
    assert items[0].time == 145900
    assert items[0].close == 10.8


def test_dataframe_to_json_rows_serializes_timestamp_and_numpy_values():
    df = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-02")],
            "count": [pd.Series([1], dtype="int64").iloc[0]],
            "value": [pd.Series([1.5], dtype="float64").iloc[0]],
        }
    )

    rows = dataframe_to_json_rows(df)

    assert rows == [["2024-01-02T00:00:00", 1, 1.5]]
