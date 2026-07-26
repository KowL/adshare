"""Tests for the service-status API routes."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import pytest


class _FakeRedis:
    def __init__(self) -> None:
        self.strings: Dict[str, bytes] = {}
        self.ttls: Dict[str, float] = {}
        self.streams: Dict[str, List[Any]] = {}
        self.hashes: Dict[str, Dict[str, bytes]] = {}

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        if isinstance(value, str):
            value = value.encode("utf-8")
        self.strings[key] = value
        if ex is not None:
            self.ttls[key] = time.time() + ex
        return True

    def get(self, key: str) -> Optional[bytes]:
        value = self.strings.get(key)
        if value is None:
            return None
        if key in self.ttls and self.ttls[key] < time.time():
            del self.strings[key]
            del self.ttls[key]
            return None
        return value

    def xrevrange(self, stream: str, count: Optional[int] = None) -> List[Any]:
        entries = list(self.streams.get(stream, []))
        entries.reverse()
        return entries[:count] if count is not None else entries

    def xadd(
        self, stream: str, fields: Dict[str, Any], maxlen: Optional[int] = None,
        approximate: bool = False,
    ) -> str:
        encoded = {}
        for key, value in fields.items():
            encoded[key.encode() if isinstance(key, str) else key] = (
                value.encode() if isinstance(value, str) else value
            )
        entries = self.streams.setdefault(stream, [])
        entry_id = str(len(entries))
        entries.append((entry_id, encoded))
        if maxlen is not None and len(entries) > maxlen:
            entries.pop(0)
        return entry_id

    def hgetall(self, key: str) -> Dict[str, bytes]:
        return dict(self.hashes.get(key, {}))


class _FakeCacheManager:
    def __init__(self) -> None:
        self.redis = _FakeRedis()


class _FakeWarehouse:
    def __init__(self) -> None:
        self.metrics: Dict[str, Optional[Dict[str, Any]]] = {
            "day": None, "week": None, "month": None,
        }

    def freshness_stats(self, period: str) -> Optional[Dict[str, Any]]:
        row = self.metrics[period]
        return dict(row) if row is not None else None


@pytest.fixture
def fake_cache() -> _FakeCacheManager:
    return _FakeCacheManager()


@pytest.fixture
def fake_warehouse() -> _FakeWarehouse:
    return _FakeWarehouse()


@pytest.fixture
def status_client(client, fake_cache, fake_warehouse):
    import adshare.dependencies as deps

    app = client.app
    app.dependency_overrides[deps.get_cache_manager_dep] = lambda: fake_cache
    app.dependency_overrides[deps.get_warehouse_dep] = lambda: fake_warehouse
    return client


def _seed_heartbeat(cache: _FakeCacheManager, service: str, age_sec: float = 5.0):
    payload = {
        "ts": time.time() - age_sec,
        "uptime_s": 100.0,
        "stats": {"total_received": 10, "saved_to_redis": 9,
                  "published": 9, "failed": 1, "start_time": None},
        "codes_count": 5000,
    }
    cache.redis.set(f"adshare:heartbeat:{service}", json.dumps(payload), ex=30)


def _server_row() -> Dict[str, Any]:
    return {
        "latest_trade_date": "2026-07-25",
        "latest_complete_date": "2026-07-25",
        "code_count": 5000,
        "last_sync_at": 1785076200,
    }


class TestStatusComposite:
    def test_returns_services_and_server_freshness(
        self, status_client, fake_cache, fake_warehouse,
    ):
        _seed_heartbeat(fake_cache, "amazingdata-realtime", age_sec=3.0)
        _seed_heartbeat(fake_cache, "amazingdata-batch", age_sec=10.0)
        for period in ("day", "week", "month"):
            fake_warehouse.metrics[period] = _server_row()

        resp = status_client.get("/status")
        assert resp.status_code == 200
        assert resp.headers.get("cache-control") == "no-store"
        data = resp.json()
        names = {service["name"] for service in data["services"]}
        assert {"adshare-api", "amazingdata-realtime",
                "amazingdata-batch", "redis"} == names
        realtime = next(
            service for service in data["services"]
            if service["name"] == "amazingdata-realtime"
        )
        assert realtime["alive"] is True
        assert realtime["status"] == "ok"
        assert all(not row["missing"] for row in data["data_freshness"]["rows"])

    def test_redis_freshness_is_ignored(
        self, status_client, fake_cache, fake_warehouse,
    ):
        fake_cache.redis.set(
            "adshare:data:freshness:kline:day",
            json.dumps({"latest_trade_date": "1900-01-01"}),
        )
        fake_warehouse.metrics["day"] = _server_row()
        response = status_client.get("/status/data-freshness")
        day = next(row for row in response.json()["freshness"]["rows"]
                   if row["period"] == "day")
        assert day["latest_trade_date"] == "2026-07-25"

    def test_missing_heartbeat_is_down(self, status_client, fake_cache):
        _seed_heartbeat(fake_cache, "amazingdata-batch", age_sec=2.0)
        services = status_client.get("/status/services").json()["services"]
        by_name = {service["name"]: service for service in services}
        assert by_name["amazingdata-realtime"]["status"] == "down"
        assert by_name["amazingdata-realtime"]["alive"] is False
        assert by_name["amazingdata-batch"]["status"] == "ok"

    def test_stale_heartbeat_is_degraded_or_down(self, status_client, fake_cache):
        fake_cache.redis.set(
            "adshare:heartbeat:amazingdata-realtime",
            json.dumps({"ts": time.time() - 60, "uptime_s": 1.0}),
        )
        services = status_client.get("/status/services").json()["services"]
        realtime = next(s for s in services if s["name"] == "amazingdata-realtime")
        assert realtime["status"] in ("degraded", "down")
        assert realtime["alive"] is False

    def test_missing_server_freshness_row_reported(self, status_client):
        rows = {
            row["period"]: row
            for row in status_client.get("/status/data-freshness").json()["freshness"]["rows"]
        }
        assert rows["day"]["missing"] is True
        assert rows["week"]["missing"] is True
        assert rows["month"]["missing"] is True

    def test_realtime_stats_history_roundtrip(self, status_client, fake_cache):
        _seed_heartbeat(fake_cache, "amazingdata-realtime", age_sec=1.0)
        for i in range(3):
            snapshot = {"ts": time.time() - 60 * (3 - i), "total_received": i * 100}
            fake_cache.redis.xadd(
                "adshare:stats:amazingdata-realtime",
                {"data": json.dumps(snapshot)}, maxlen=30,
            )
        stats = status_client.get("/status/realtime/stats").json()["stats"]
        assert stats["amazingdata-realtime"]["heartbeat"]["stats"]["total_received"] == 10
        assert len(stats["amazingdata-realtime"]["history"]) == 3
        assert stats["amazingdata-realtime"]["history"][-1]["total_received"] == 200

    def test_events_endpoint_returns_empty_when_unseeded(self, status_client):
        response = status_client.get("/status/events")
        assert response.status_code == 200
        assert response.json()["events"] == []
