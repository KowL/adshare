"""Historical data warehouse module (L3 cache).

Provides persistent Parquet storage of K-line, calendar, and code metadata
on top of DuckDB for in-process SQL queries.
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
from adshare.historical.sync import (
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
    "sync_kline_daily",
    "sync_kline_weekly",
    "sync_kline_monthly",
    "sync_meta_codes",
    "sync_meta_calendar",
    "SyncResult",
    "init_scheduler",
    "start_scheduler",
    "shutdown_scheduler",
]
