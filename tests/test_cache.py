"""Tests for Redis-only cache manager semantics."""

from types import SimpleNamespace

from adshare.core.cache import CacheManager


class FakeRedis:
    def __init__(self) -> None:
        self.values = {}
        self.ttls = {}

    def get(self, key: str):
        return self.values.get(key)

    def setex(self, key: str, ttl: int, value: bytes):
        self.values[key] = value
        self.ttls[key] = ttl

    def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values:
                deleted += 1
                del self.values[key]
        return deleted

    def ping(self) -> bool:
        return True

    def info(self, section: str):
        return {"used_memory_human": "1M"}


def make_cache() -> CacheManager:
    settings = SimpleNamespace(
        cache_key_prefix="adshare",
        cache_ttl_realtime=123,
        redis_host="localhost",
        redis_port=6379,
        redis_db=0,
        redis_password=None,
        redis_max_connections=1,
    )
    cache = CacheManager(settings=settings)
    cache._redis = FakeRedis()
    return cache


def test_realtime_market_cache_uses_realtime_ttl():
    cache = make_cache()

    ok = cache.set_realtime_market({"price": 10.1}, "snapshot", "000001.SZ")

    assert ok is True
    assert cache.get_realtime_market("snapshot", "000001.SZ") == {"price": 10.1}
    assert cache.redis.ttls["adshare:realtime:snapshot:000001.SZ"] == 123


def test_cache_health_reports_redis_purpose_without_local_cache_fields():
    cache = make_cache()

    health = cache.health()

    assert health["redis_connected"] is True
    assert health["purpose"] == "real_time_market_data"
    assert "local_cache_path" not in health
    assert "local_cache_size_mb" not in health
