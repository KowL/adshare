"""Configuration management for adshare (API service).

This module owns the **shared** configuration that both the API process
(``adshare``) and the worker processes (``amazingdata.batch`` /
``amazingdata.realtime``) need to read from — Redis, the L3 historical
warehouse, app-level knobs, rate limiting, auth.

The worker-only fields (AmazingData login, sync schedule, realtime
subscription, idempotent repair schedule) live in
:mod:`amazingdata.config` so the API image does not need to know about
them.
"""

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # App
    # ------------------------------------------------------------------
    app_name: str = Field(default="adshare", alias="ADSHARE_APP_NAME")
    app_version: str = Field(default="0.1.0", alias="ADSHARE_APP_VERSION")
    app_host: str = Field(default="0.0.0.0", alias="ADSHARE_HOST")
    app_port: int = Field(default=8000, alias="ADSHARE_PORT")
    log_level: str = Field(default="INFO", alias="ADSHARE_LOG_LEVEL")
    debug: bool = Field(default=False, alias="ADSHARE_DEBUG")

    # ------------------------------------------------------------------
    # Redis (shared: API reads realtime, worker writes realtime)
    # ------------------------------------------------------------------
    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")
    redis_password: Optional[str] = Field(default=None, alias="REDIS_PASSWORD")
    redis_max_connections: int = Field(default=50, alias="REDIS_MAX_CONNECTIONS")

    # Redis is reserved for real-time/subscription market data.
    cache_ttl_realtime: int = Field(default=300, alias="CACHE_TTL_REALTIME")
    cache_key_prefix: str = Field(default="adshare", alias="CACHE_KEY_PREFIX")

    # ------------------------------------------------------------------
    # Rate limiting / auth / metrics
    # ------------------------------------------------------------------
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_per_minute: int = Field(default=120, alias="RATE_LIMIT_PER_MINUTE")
    rate_limit_per_second: int = Field(default=10, alias="RATE_LIMIT_PER_SECOND")

    auth_enabled: bool = Field(default=False, alias="AUTH_ENABLED")
    api_key: Optional[str] = Field(default=None, alias="ADSHARE_API_KEY")

    metrics_enabled: bool = Field(default=True, alias="METRICS_ENABLED")
    metrics_path: str = Field(default="/metrics", alias="METRICS_PATH")

    # ------------------------------------------------------------------
    # Data layer (shared: API reads L3, worker writes L3)
    # ------------------------------------------------------------------
    kline_max_limit: int = Field(default=10000, alias="KLINE_MAX_LIMIT")
    max_codes_per_query: int = Field(default=50, alias="MAX_CODES_PER_QUERY")
    default_begin_date: int = Field(default=19900101, alias="DEFAULT_BEGIN_DATE")

    # Realtime kline periods (shared between API and worker:
    # API subscribes to these Pub/Sub channels for SSE/WS broadcast).
    realtime_kline_periods: List[str] = Field(
        default=["min1", "min5", "min15", "min30", "min60"],
        alias="REALTIME_KLINE_PERIODS",
    )

    # Realtime kline history Stream (consumed by tushare rt_min).
    realtime_kline_history_ttl: int = Field(
        default=86400,
        alias="REALTIME_KLINE_HISTORY_TTL",
        description="K 线 Stream 在 Redis 中的 TTL（秒），盘后自然过期",
    )
    realtime_kline_max_bars: int = Field(
        default=240,
        alias="REALTIME_KLINE_MAX_BARS",
        description="单只股票单个 freq Stream 最多保留的根数（XADD MAXLEN）",
    )

    # Worker toggles exposed to operators via /admin endpoints.
    # The actual values used at runtime live in amazingdata.config.WorkerSettings.
    sync_schedule_enabled: bool = Field(default=True, alias="SYNC_SCHEDULE_ENABLED")
    realtime_enabled: bool = Field(default=True, alias="REALTIME_ENABLED")

    historical_enabled: bool = Field(default=True, alias="HISTORICAL_ENABLED")
    historical_path: str = Field(default="./data", alias="HISTORICAL_PATH")
    historical_retention_years: int = Field(default=0, alias="HISTORICAL_RETENTION_YEARS")

    duckdb_mode: str = Field(default="memory", alias="DUCKDB_MODE")
    duckdb_file_path: str = Field(
        default="./data/duckdb/adshare.duckdb", alias="DUCKDB_FILE_PATH"
    )
    duckdb_max_rows: int = Field(default=100000, alias="DUCKDB_MAX_ROWS")
    duckdb_query_timeout: int = Field(default=30, alias="DUCKDB_QUERY_TIMEOUT")

    @property
    def redis_url(self) -> str:
        """Return Redis connection URL."""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
