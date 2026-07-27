"""PostgreSQL historical market-data repository.

Provides persistent PostgreSQL storage of K-line, calendar, code metadata,
and less frequently queried AmazingData reference datasets.

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
    normalize_period,
)
from adshare.historical.warehouse import HistoricalWarehouse, get_warehouse

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
    "normalize_period",
    "HistoricalWarehouse",
    "get_warehouse",
]
