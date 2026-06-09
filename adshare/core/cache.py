"""Redis cache manager for real-time market data.

Historical K-line and metadata persistence belongs to
``adshare.historical``. This module intentionally does not maintain a local
request-result cache.
"""

import hashlib
import pickle
from typing import Any, Optional

import redis
from redis.connection import ConnectionPool

from adshare.core.config import Settings, get_settings


class CacheManager:
    """Manage Redis values used for real-time market data."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._redis: Optional[redis.Redis] = None
        self._redis_pool: Optional[ConnectionPool] = None

    @property
    def redis(self) -> redis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            self._redis_pool = ConnectionPool(
                host=self.settings.redis_host,
                port=self.settings.redis_port,
                db=self.settings.redis_db,
                password=self.settings.redis_password or None,
                max_connections=self.settings.redis_max_connections,
                socket_timeout=5,
                socket_connect_timeout=5,
            )
            self._redis = redis.Redis(connection_pool=self._redis_pool)
        return self._redis

    def _make_key(self, *parts: str) -> str:
        """Create a cache key from parts."""
        key = ":".join([self.settings.cache_key_prefix, *parts])
        # Hash if too long
        if len(key) > 200:
            key_hash = hashlib.sha256(key.encode()).hexdigest()[:16]
            key = f"{self.settings.cache_key_prefix}:hash:{key_hash}"
        return key

    def _get_ttl(self, data_type: str) -> int:
        """Get TTL based on data type."""
        ttl_map = {
            "realtime": self.settings.cache_ttl_realtime,
            "realtime_snapshot": self.settings.cache_ttl_realtime,
            "subscription": self.settings.cache_ttl_realtime,
            "snapshot": self.settings.cache_ttl_realtime,
        }
        return ttl_map.get(data_type, self.settings.cache_ttl_realtime)

    # ============================================================
    # Redis real-time market state
    # ============================================================

    def get(self, data_type: str, *key_parts: str) -> Optional[Any]:
        """Get data from Redis."""
        try:
            key = self._make_key(data_type, *key_parts)
            data = self.redis.get(key)
            if data is None:
                return None
            return pickle.loads(data)
        except Exception:
            return None

    def set(
        self,
        data_type: str,
        value: Any,
        *key_parts: str,
        ttl: Optional[int] = None,
    ) -> bool:
        """Set data in Redis."""
        try:
            key = self._make_key(data_type, *key_parts)
            ttl = ttl or self._get_ttl(data_type)
            serialized = pickle.dumps(value)
            self.redis.setex(key, ttl, serialized)
            return True
        except Exception:
            return False

    def delete(self, data_type: str, *key_parts: str) -> bool:
        """Delete data from Redis."""
        try:
            key = self._make_key(data_type, *key_parts)
            self.redis.delete(key)
            return True
        except Exception:
            return False

    def get_realtime_market(self, *key_parts: str) -> Optional[Any]:
        """Get real-time market data from Redis."""
        return self.get("realtime", *key_parts)

    def set_realtime_market(
        self,
        value: Any,
        *key_parts: str,
        ttl: Optional[int] = None,
    ) -> bool:
        """Set real-time market data in Redis."""
        return self.set("realtime", value, *key_parts, ttl=ttl)

    def clear(self, pattern: str = "*") -> int:
        """Clear cache by pattern."""
        try:
            key_pattern = f"{self.settings.cache_key_prefix}:{pattern}"
            keys = self.redis.keys(key_pattern)
            if keys:
                return self.redis.delete(*keys)
            return 0
        except Exception:
            return 0

    def health(self) -> dict:
        """Check cache health."""
        try:
            self.redis.ping()
            redis_ok = True
            redis_info = self.redis.info("memory")
        except Exception:
            redis_ok = False
            redis_info = {}

        return {
            "redis_connected": redis_ok,
            "redis_memory": redis_info.get("used_memory_human", "unknown"),
            "purpose": "real_time_market_data",
        }


# Singleton instance
_cache_manager: Optional[CacheManager] = None


def get_cache_manager() -> CacheManager:
    """Get singleton cache manager instance."""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager
