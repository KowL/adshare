"""Historical data warehouse module (L3 cache).

Provides persistent Parquet storage of K-line, calendar, and code metadata
on top of DuckDB for in-process SQL queries.

This package is read-only with respect to data sources: the sync jobs that
populate the warehouse live in the worker package
(:mod:`amazingdata.batch`).
"""

from adshare.historical.models import (
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
)
from adshare.historical.warehouse import HistoricalWarehouse, get_warehouse
from adshare.historical.maintenance import (
    MaintenanceResult,
    repair_kline_directory,
    repair_codes_table,
    repair_financial_table,
    repair_all,
)

__all__ = [
    "KLINE_COLUMNS",
    "KLINE_DTYPES",
    "CALENDAR_COLUMNS",
    "CALENDAR_DTYPES",
    "CODES_COLUMNS",
    "CODES_DTYPES",
    "validate_kline_df",
    "standardize_kline_df",
    "standardize_calendar_df",
    "standardize_codes_df",
    "kline_file_path",
    "period_to_subdir",
    "normalize_period",
    "HistoricalWarehouse",
    "get_warehouse",
    "MaintenanceResult",
    "repair_kline_directory",
    "repair_codes_table",
    "repair_financial_table",
    "repair_all",
]
