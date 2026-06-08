"""Configuration management for adshare."""

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App settings
    app_name: str = Field(default="adshare", alias="ADSHARE_APP_NAME")
    app_version: str = Field(default="0.1.0", alias="ADSHARE_APP_VERSION")
    app_host: str = Field(default="0.0.0.0", alias="ADSHARE_HOST")
    app_port: int = Field(default=8000, alias="ADSHARE_PORT")
    log_level: str = Field(default="INFO", alias="ADSHARE_LOG_LEVEL")
    debug: bool = Field(default=False, alias="ADSHARE_DEBUG")

    # AmazingData settings
    ad_username: str = Field(default="", alias="AD_USERNAME")
    ad_password: str = Field(default="", alias="AD_PASSWORD")
    ad_host: str = Field(default="localhost", alias="AD_HOST")
    ad_port: int = Field(default=8600, alias="AD_PORT")
    ad_pool_size: int = Field(default=5, alias="AD_POOL_SIZE")
    ad_max_retries: int = Field(default=3, alias="AD_MAX_RETRIES")
    ad_retry_delay: float = Field(default=1.0, alias="AD_RETRY_DELAY")
    ad_login_timeout: int = Field(default=30, alias="AD_LOGIN_TIMEOUT")

    # Redis settings
    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")
    redis_password: Optional[str] = Field(default=None, alias="REDIS_PASSWORD")
    redis_max_connections: int = Field(default=50, alias="REDIS_MAX_CONNECTIONS")

    # Cache settings
    cache_ttl_short: int = Field(default=300, alias="CACHE_TTL_SHORT")
    cache_ttl_medium: int = Field(default=3600, alias="CACHE_TTL_MEDIUM")
    cache_ttl_long: int = Field(default=86400, alias="CACHE_TTL_LONG")
    cache_local_enabled: bool = Field(default=True, alias="LOCAL_CACHE_ENABLED")
    cache_local_path: str = Field(default="./cache", alias="LOCAL_CACHE_PATH")
    cache_key_prefix: str = Field(default="adshare", alias="CACHE_KEY_PREFIX")

    # Rate limiting
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_per_minute: int = Field(default=120, alias="RATE_LIMIT_PER_MINUTE")
    rate_limit_per_second: int = Field(default=10, alias="RATE_LIMIT_PER_SECOND")

    # Auth
    auth_enabled: bool = Field(default=False, alias="AUTH_ENABLED")
    api_key: Optional[str] = Field(default=None, alias="ADSHARE_API_KEY")

    # MCP
    mcp_enabled: bool = Field(default=True, alias="MCP_ENABLED")
    mcp_transport: str = Field(default="sse", alias="MCP_TRANSPORT")
    mcp_path: str = Field(default="/mcp", alias="MCP_PATH")

    # Metrics
    metrics_enabled: bool = Field(default=True, alias="METRICS_ENABLED")
    metrics_path: str = Field(default="/metrics", alias="METRICS_PATH")

    # Data settings
    kline_max_limit: int = Field(default=10000, alias="KLINE_MAX_LIMIT")
    max_codes_per_query: int = Field(default=50, alias="MAX_CODES_PER_QUERY")
    default_begin_date: int = Field(default=19900101, alias="DEFAULT_BEGIN_DATE")

    @property
    def amazingdata_connection_string(self) -> str:
        """Return AmazingData connection info string (without password)."""
        return f"{self.ad_username}@{self.ad_host}:{self.ad_port}"

    @property
    def redis_url(self) -> str:
        """Return Redis connection URL."""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
