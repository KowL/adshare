"""Worker-only configuration for the AmazingData subsystem.

Holds the fields that only ``amazingdata.batch`` and
``amazingdata.realtime`` need:

* AmazingData SDK connection (login + retry tuning)
* Realtime subscription toggle + periods
* APScheduler cron timings for K-line / meta / reference sync
* Idempotent warehouse maintenance schedule
* Index codes for the index-component sync

Shared settings (Redis, L3 warehouse, app-level knobs) live in
:mod:`adshare.core.config` and are exposed as read-only properties on
:class:`WorkerSettings` so callers can use a single ``settings`` object
without juggling two caches.
"""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from adshare.core.config import Settings as _SharedSettings
from adshare.core.config import get_settings as _get_shared_settings


class WorkerSettings(BaseSettings):
    """Settings for the AmazingData worker (batch + realtime).

    Worker-only fields are declared directly. Shared fields (Redis,
    L3 warehouse, app knobs) are accessed via the :func:`shared`
    property so existing call sites can keep using ``settings.<field>``
    uniformly.
    """

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # AmazingData SDK connection
    # ------------------------------------------------------------------
    ad_username: str = Field(default="", alias="AD_USERNAME")
    ad_password: str = Field(default="", alias="AD_PASSWORD")
    ad_host: str = Field(default="localhost", alias="AD_HOST")
    ad_port: int = Field(default=8600, alias="AD_PORT")
    ad_pool_size: int = Field(default=5, alias="AD_POOL_SIZE")
    ad_max_retries: int = Field(default=3, alias="AD_MAX_RETRIES")
    ad_retry_delay: float = Field(default=1.0, alias="AD_RETRY_DELAY")
    ad_login_timeout: int = Field(default=30, alias="AD_LOGIN_TIMEOUT")

    # ------------------------------------------------------------------
    # Realtime subscription
    # ------------------------------------------------------------------
    realtime_enabled: bool = Field(default=True, alias="REALTIME_ENABLED")

    # ------------------------------------------------------------------
    # Sync scheduler (batch mode)
    # ------------------------------------------------------------------
    sync_schedule_enabled: bool = Field(default=True, alias="SYNC_SCHEDULE_ENABLED")
    sync_on_start: bool = Field(default=False, alias="SYNC_ON_START")
    sync_kline_daily_hour: int = Field(default=17, alias="SYNC_KLINE_DAILY_HOUR")
    sync_kline_daily_minute: int = Field(default=10, alias="SYNC_KLINE_DAILY_MINUTE")
    sync_kline_weekly_hour: int = Field(default=19, alias="SYNC_KLINE_WEEKLY_HOUR")
    sync_kline_weekly_minute: int = Field(default=30, alias="SYNC_KLINE_WEEKLY_MINUTE")
    sync_kline_monthly_hour: int = Field(default=20, alias="SYNC_KLINE_MONTHLY_HOUR")
    sync_kline_monthly_minute: int = Field(default=0, alias="SYNC_KLINE_MONTHLY_MINUTE")
    sync_meta_codes_hour: int = Field(default=8, alias="SYNC_META_CODES_HOUR")
    sync_meta_codes_minute: int = Field(default=0, alias="SYNC_META_CODES_MINUTE")
    sync_shareholder_day_of_week: str = Field(
        default="sat", alias="SYNC_SHAREHOLDER_DAY_OF_WEEK"
    )
    sync_shareholder_hour: int = Field(default=3, alias="SYNC_SHAREHOLDER_HOUR")
    sync_shareholder_minute: int = Field(default=0, alias="SYNC_SHAREHOLDER_MINUTE")
    sync_index_component_day_of_week: str = Field(
        default="sat", alias="SYNC_INDEX_COMPONENT_DAY_OF_WEEK"
    )
    sync_index_component_hour: int = Field(default=4, alias="SYNC_INDEX_COMPONENT_HOUR")
    sync_index_component_minute: int = Field(
        default=0, alias="SYNC_INDEX_COMPONENT_MINUTE"
    )
    sync_workers: int = Field(default=4, alias="SYNC_WORKERS")
    sync_retry_attempts: int = Field(default=3, alias="SYNC_RETRY_ATTEMPTS")

    # Comma-separated index codes for the index-component sync;
    # empty means "use the built-in default list".
    index_codes: str = Field(default="", alias="INDEX_CODES")

    # ------------------------------------------------------------------
    # Maintenance (idempotent L3 warehouse repair) schedule
    # ------------------------------------------------------------------
    maintenance_schedule_enabled: bool = Field(
        default=False, alias="MAINTENANCE_SCHEDULE_ENABLED"
    )
    maintenance_kline_day_of_week: str = Field(
        default="sun", alias="MAINTENANCE_KLINE_DAY_OF_WEEK"
    )
    maintenance_kline_hour: int = Field(default=3, alias="MAINTENANCE_KLINE_HOUR")
    maintenance_kline_minute: int = Field(default=0, alias="MAINTENANCE_KLINE_MINUTE")
    maintenance_financial_day_of_week: str = Field(
        default="sun", alias="MAINTENANCE_FINANCIAL_DAY_OF_WEEK"
    )
    maintenance_financial_hour: int = Field(default=4, alias="MAINTENANCE_FINANCIAL_HOUR")
    maintenance_financial_minute: int = Field(
        default=0, alias="MAINTENANCE_FINANCIAL_MINUTE"
    )

    # SDK local cache dir (InfoData writes HDF5 here).
    amazingdata_local_path: str = Field(
        default="/app/data/sdk_cache", alias="AMAZINGDATA_LOCAL_PATH"
    )

    def __getattr__(self, name: str):
        # Forward shared Settings fields (Redis / L3 / app) so call sites
        # can use ``settings.<field>`` uniformly regardless of whether
        # they hold a Settings or WorkerSettings instance.
        # Avoid recursion for pydantic internals.
        if name.startswith("_") or name in {
            "model_config", "model_fields", "model_extra",
        }:
            raise AttributeError(name)
        try:
            return getattr(_get_shared_settings(), name)
        except AttributeError:
            raise AttributeError(
                f"'WorkerSettings' object has no attribute {name!r}"
            )

    @property
    def shared(self) -> _SharedSettings:
        """Return the shared Settings instance (Redis/L3/app)."""
        return _get_shared_settings()

    @property
    def amazingdata_connection_string(self) -> str:
        """Return AmazingData connection info string (without password)."""
        return f"{self.ad_username}@{self.ad_host}:{self.ad_port}"

    # Convenience properties for the most-used shared fields so call
    # sites don't need to spell out ``settings.shared.historical_path``.
    @property
    def historical_path(self) -> str:
        return self.shared.historical_path

    @property
    def historical_enabled(self) -> bool:
        return self.shared.historical_enabled

    @property
    def redis_url(self) -> str:
        return self.shared.redis_url


@lru_cache()
def get_worker_settings() -> WorkerSettings:
    """Get cached worker settings instance."""
    return WorkerSettings()
