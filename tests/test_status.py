"""Tests for the service-status API routes."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import pytest


class _FakeRedis:
    """In-memory stand-in for the redis client surface used by
    ``adshare.routers.status``."""

    def __init__(self) -> None:
        self.strings: Dict[str, bytes] = {}
        self.ttls: Dict[str, float] = {}
        self.streams: Dict[str, List[Any]] = {}
        self.hashes: Dict[str, Dict[str, bytes]] = {}

    # String
    def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        if isinstance(value, str):
            value = value.encode("utf-8")
        self.strings[key] = value
        if ex is not None:
            self.ttls[key] = time.time() + ex
        return True

    def get(self, key: str) -> Optional[bytes]:
        v = self.strings.get(key)
        if v is None:
            return None
        if key in self.ttls and self.ttls[key] < time.time():
            del self.strings[key]
            del self.ttls[key]
            return None
        return v

    # Stream
    def xrevrange(
        self, stream: str, count: Optional[int] = None,
    ) -> List[Any]:
        entries = list(self.streams.get(stream, []))
        entries.reverse()
        if count is not None:
            entries = entries[:count]
        return entries

    def xadd(
        self, stream: str, fields: Dict[str, Any], maxlen: Optional[int] = None,
        approximate: bool = False,
    ) -> str:
        encoded = {}
        for k, v in fields.items():
            kb = k.encode("utf-8") if isinstance(k, str) else k
            if isinstance(v, str):
                v = v.encode("utf-8")
            encoded[kb] = v
        entries = self.streams.setdefault(stream, [])
        entry_id = f"{len(entries)}"
        entries.append((entry_id, encoded))
        if maxlen is not None and len(entries) > maxlen:
            entries.pop(0)
        return entry_id

    # Hash
    def hgetall(self, key: str) -> Dict[str, bytes]:
        return dict(self.hashes.get(key, {}))


class _FakeCacheManager:
    def __init__(self) -> None:
        self.redis = _FakeRedis()


@pytest.fixture
def fake_cache() -> _FakeCacheManager:
    return _FakeCacheManager()


@pytest.fixture
def status_client(client, fake_cache, monkeypatch):
    """TestClient with the cache manager replaced by a fake."""
    import adshare.dependencies as _deps_mod

    app = client.app
    app.dependency_overrides[_deps_mod.get_cache_manager_dep] = (
        lambda: fake_cache
    )
    return client


def _seed_heartbeat(fake_cache: _FakeCacheManager, svc: str, age_sec: float = 5.0):
    payload = {
        "ts": time.time() - age_sec,
        "uptime_s": 100.0,
        "stats": {"total_received": 10, "saved_to_redis": 9,
                  "published": 9, "failed": 1, "start_time": None},
        "codes_count": 5000,
    }
    fake_cache.redis.set(
        f"adshare:heartbeat:{svc}",
        json.dumps(payload),
        ex=30,
    )


def _seed_freshness(fake_cache: _FakeCacheManager, period: str, in_progress: bool):
    row = {
        "period": period,
        "latest_trade_date": "2026-07-25",
        "latest_complete_date": "2026-07-24" if in_progress else "2026-07-25",
        "code_count": 5000,
        "last_run_at": "2026-07-26T17:10:00+08:00",
        "last_run_status": "ok",
        "last_error": None,
        "rows_inserted": 5000,
        "is_in_progress": in_progress,
    }
    fake_cache.redis.set(
        f"adshare:data:freshness:kline:{period}",
        json.dumps(row),
        ex=86400,
    )


class TestStatusComposite:
    def test_returns_services_and_freshness(self, status_client, fake_cache):
        _seed_heartbeat(fake_cache, "amazingdata-realtime", age_sec=3.0)
        _seed_heartbeat(fake_cache, "amazingdata-batch", age_sec=10.0)
        _seed_freshness(fake_cache, "day", in_progress=False)
        _seed_freshness(fake_cache, "week", in_progress=False)
        _seed_freshness(fake_cache, "month", in_progress=False)

        resp = status_client.get("/status")
        assert resp.status_code == 200
        assert resp.headers.get("cache-control") == "no-store"
        data = resp.json()

        names = {s["name"] for s in data["services"]}
        assert {"adshare-api", "amazingdata-realtime",
                "amazingdata-batch", "redis"} == names

        realtime = next(
            s for s in data["services"] if s["name"] == "amazingdata-realtime"
        )
        assert realtime["alive"] is True
        assert realtime["status"] == "ok"

        periods = {r["period"] for r in data["data_freshness"]["rows"]}
        assert periods == {"day", "week", "month"}

    def test_missing_heartbeat_is_down(self, status_client, fake_cache):
        # realtime heartbeat absent → down
        _seed_heartbeat(fake_cache, "amazingdata-batch", age_sec=2.0)

        resp = status_client.get("/status/services")
        assert resp.status_code == 200
        services = {s["name"]: s for s in resp.json()["services"]}
        assert services["amazingdata-realtime"]["status"] == "down"
        assert services["amazingdata-realtime"]["alive"] is False
        assert services["amazingdata-batch"]["status"] == "ok"
        assert services["adshare-api"]["status"] == "ok"

    def test_stale_heartbeat_is_degraded_or_down(
        self, status_client, fake_cache,
    ):
        # age_sec=60 → older than 30 s TTL was overridden to bypass TTL
        # by writing directly; result must be "degraded" or "down".
        payload = {"ts": time.time() - 60, "uptime_s": 1.0}
        fake_cache.redis.set(
            "adshare:heartbeat:amazingdata-realtime",
            json.dumps(payload),
        )
        resp = status_client.get("/status/services")
        rt = next(
            s for s in resp.json()["services"]
            if s["name"] == "amazingdata-realtime"
        )
        assert rt["status"] in ("degraded", "down")
        assert rt["alive"] is False

    def test_missing_freshness_row_reported(self, status_client, fake_cache):
        resp = status_client.get("/status/data-freshness")
        rows = {r["period"]: r for r in resp.json()["freshness"]["rows"]}
        assert rows["day"]["missing"] is True
        assert rows["week"]["missing"] is True
        assert rows["month"]["missing"] is True

    def test_in_progress_flag_surfaces(self, status_client, fake_cache):
        _seed_freshness(fake_cache, "day", in_progress=True)
        resp = status_client.get("/status/data-freshness")
        rows = {r["period"]: r for r in resp.json()["freshness"]["rows"]}
        assert rows["day"]["is_in_progress"] is True
        assert rows["day"]["latest_complete_date"] == "2026-07-24"

    def test_realtime_stats_history_roundtrip(self, status_client, fake_cache):
        _seed_heartbeat(fake_cache, "amazingdata-realtime", age_sec=1.0)
        # Seed a 1-min counter snapshot
        for i in range(3):
            snap = {"ts": time.time() - 60 * (3 - i), "total_received": i * 100}
            fake_cache.redis.xadd(
                "adshare:stats:amazingdata-realtime",
                {"data": json.dumps(snap)},
                maxlen=30,
            )

        resp = status_client.get("/status/realtime/stats")
        assert resp.status_code == 200
        stats = resp.json()["stats"]["amazingdata-realtime"]
        assert stats["heartbeat"]["stats"]["total_received"] == 10
        assert len(stats["history"]) == 3
        assert stats["history"][-1]["total_received"] == 200

    def test_events_endpoint_returns_empty_when_unseeded(
        self, status_client, fake_cache,
    ):
        resp = status_client.get("/status/events")
        assert resp.status_code == 200
        assert resp.json()["events"] == []