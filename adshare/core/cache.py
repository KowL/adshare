"""Multi-layer cache manager for adshare."""

import hashlib
import json
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import redis
from redis.connection import ConnectionPool

from adshare.core.config import Settings, get_settings


class CacheManager:
    """Manages L1 (Redis) and L2 (local file) caches."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._redis: Optional[redis.Redis] = None
        self._redis_pool: Optional[ConnectionPool] = None
        self._local_cache_path = Path(self.settings.cache_local_path)
        self._local_cache_path.mkdir(parents=True, exist_ok=True)

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
            "code_list": self.settings.cache_ttl_short,
            "code_info": self.settings.cache_ttl_short,
            "snapshot": self.settings.cache_ttl_short,
            "kline": self.settings.cache_ttl_medium,
            "financial": self.settings.cache_ttl_long,
            "shareholder": self.settings.cache_ttl_long,
            "technical": self.settings.cache_ttl_medium,
            "fundamental": self.settings.cache_ttl_medium,
            "factor": self.settings.cache_ttl_medium,
            "calendar": self.settings.cache_ttl_long,
        }
        return ttl_map.get(data_type, self.settings.cache_ttl_medium)

    # ============================================================
    # L1: Redis Cache
    # ============================================================

    def get(self, data_type: str, *key_parts: str) -> Optional[Any]:
        """Get data from L1 cache."""
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
        """Set data in L1 cache."""
        try:
            key = self._make_key(data_type, *key_parts)
            ttl = ttl or self._get_ttl(data_type)
            serialized = pickle.dumps(value)
            self.redis.setex(key, ttl, serialized)
            return True
        except Exception:
            return False

    def delete(self, data_type: str, *key_parts: str) -> bool:
        """Delete data from L1 cache."""
        try:
            key = self._make_key(data_type, *key_parts)
            self.redis.delete(key)
            return True
        except Exception:
            return False

    # ============================================================
    # L2: Local File Cache (Parquet/JSON)
    # ============================================================

    def _local_path(self, data_type: str, *key_parts: str) -> Path:
        """Get local cache file path."""
        key = self._make_key(data_type, *key_parts)
        # Replace filesystem-unsafe chars; keep alnum, hyphen, underscore
        safe_key = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
        # Truncate extremely long names while keeping uniqueness via hash suffix
        if len(safe_key) > 100:
            short_hash = hashlib.sha256(safe_key.encode()).hexdigest()[:12]
            safe_key = safe_key[:80] + "_" + short_hash
        return self._local_cache_path / f"{safe_key}.parquet"

    def get_local(self, data_type: str, *key_parts: str) -> Optional[pd.DataFrame]:
        """Get DataFrame from L2 cache."""
        if not self.settings.cache_local_enabled:
            return None
        path = self._local_path(data_type, *key_parts)
        if not path.exists():
            return None
        try:
            # Check if cache is too old (1 day)
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            if datetime.now() - mtime > timedelta(days=1):
                path.unlink(missing_ok=True)
                return None
            return pd.read_parquet(path)
        except Exception:
            return None

    def set_local(
        self, data_type: str, df: pd.DataFrame, *key_parts: str
    ) -> bool:
        """Save DataFrame to L2 cache."""
        if not self.settings.cache_local_enabled:
            return False
        try:
            path = self._local_path(data_type, *key_parts)
            df.to_parquet(path, compression="zstd")
            return True
        except Exception:
            return False

    # ============================================================
    # Unified Get/Set with L1 -> L2 fallback
    # ============================================================

    def get_unified(self, data_type: str, *key_parts: str) -> Optional[Any]:
        """Try L1 first, then L2."""
        # Try L1 (Redis)
        data = self.get(data_type, *key_parts)
        if data is not None:
            return data

        # Try L2 (Local) - only for DataFrame-like data
        if data_type in ("kline", "snapshot", "financial"):
            df = self.get_local(data_type, *key_parts)
            if df is not None:
                # Promote to L1
                self.set(data_type, df, *key_parts)
                return df

        return None

    def set_unified(
        self,
        data_type: str,
        value: Any,
        *key_parts: str,
        ttl: Optional[int] = None,
    ) -> bool:
        """Set in both L1 and L2."""
        l1_ok = self.set(data_type, value, *key_parts, ttl=ttl)

        # Also save DataFrames to L2
        if isinstance(value, pd.DataFrame):
            self.set_local(data_type, value, *key_parts)

        return l1_ok

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
            "local_cache_enabled": self.settings.cache_local_enabled,
            "local_cache_path": str(self._local_cache_path),
            "local_cache_size_mb": self._get_local_cache_size(),
        }

    def _get_local_cache_size(self) -> float:
        """Get total size of local cache in MB."""
        total = 0
        for f in self._local_cache_path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return round(total / (1024 * 1024), 2)


# Singleton instance
_cache_manager: Optional[CacheManager] = None


def get_cache_manager() -> CacheManager:
    """Get singleton cache manager instance."""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager
