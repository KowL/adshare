"""Tests for the shared HeartbeatWriter."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional

import pytest


class FakeRedis:
    """Minimal in-memory Redis stand-in for HeartbeatWriter tests."""

    def __init__(self) -> None:
        self.strings: Dict[str, bytes] = {}
        self.ttls: Dict[str, float] = {}
        self.streams: Dict[str, List[Any]] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        with self._lock:
            if isinstance(value, str):
                value = value.encode("utf-8")
            self.strings[key] = value
            if ex is not None:
                self.ttls[key] = time.time() + ex
            else:
                self.ttls.pop(key, None)
            return True

    def get(self, key: str) -> Optional[bytes]:
        with self._lock:
            v = self.strings.get(key)
            if v is None:
                return None
            if key in self.ttls and self.ttls[key] < time.time():
                del self.strings[key]
                del self.ttls[key]
                return None
            return v

    def xadd(
        self,
        stream: str,
        fields: Dict[str, Any],
        maxlen: Optional[int] = None,
        approximate: bool = False,
    ) -> str:
        with self._lock:
            entries = self.streams.setdefault(stream, [])
            encoded_id = f"{int(time.time() * 1000)}-{len(entries)}"
            encoded_fields: Dict[bytes, bytes] = {}
            for k, v in fields.items():
                kb = k.encode("utf-8") if isinstance(k, str) else k
                if isinstance(v, str):
                    v = v.encode("utf-8")
                encoded_fields[kb] = v
            entries.append((encoded_id, encoded_fields))
            if maxlen is not None and len(entries) > maxlen:
                entries.pop(0)
            return encoded_id

    def xrevrange(
        self, stream: str, count: Optional[int] = None,
    ) -> List[Any]:
        with self._lock:
            entries = list(self.streams.get(stream, []))
        entries.reverse()
        if count is not None:
            entries = entries[:count]
        return entries


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


def _make_writer(
    fake_redis: FakeRedis,
    payload: Dict[str, Any],
    snapshot: Optional[Dict[str, Any]] = None,
    interval_sec: float = 0.05,
):
    from amazingdata.heartbeat import HeartbeatWriter

    payload_box = {"v": payload}
    snapshot_box = {"v": snapshot or {}}

    def get_payload():
        return dict(payload_box["v"])

    def get_snapshot():
        return dict(snapshot_box["v"])

    writer = HeartbeatWriter(
        service_name="test-svc",
        get_payload=get_payload,
        get_counter_snapshot=get_snapshot,
        interval_sec=interval_sec,
        redis_client=fake_redis,
    )
    return writer, payload_box, snapshot_box


def test_heartbeat_writes_json_with_ttl(fake_redis: FakeRedis):
    writer, payload_box, _ = _make_writer(
        fake_redis, {"uptime_s": 1.0, "stats": {"x": 0}},
    )
    writer.start()
    time.sleep(0.2)
    writer.stop()

    raw = fake_redis.get("adshare:heartbeat:test-svc")
    assert raw is not None
    parsed = json.loads(raw)
    assert "ts" in parsed
    assert parsed["uptime_s"] == 1.0


def test_heartbeat_records_initial_snapshot_after_minute_window(
    fake_redis: FakeRedis,
):
    """At every 60s boundary the writer should XADD a snapshot."""
    writer, _, _ = _make_writer(
        fake_redis, {"x": 1}, snapshot={"received": 5},
        interval_sec=0.01,
    )
    writer.start()
    # Force a snapshot by patching the internal timestamp.
    writer._last_history_ts = 0.0
    time.sleep(0.3)
    writer.stop()

    entries = fake_redis.streams.get("adshare:stats:test-svc", [])
    assert len(entries) >= 1
    entry_id, fields = entries[0]
    raw = fields.get(b"data") or fields.get("data")
    assert raw is not None
    parsed = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    assert parsed.get("received") == 5
    assert "ts" in parsed


def test_heartbeat_stop_terminates_thread(fake_redis: FakeRedis):
    writer, _, _ = _make_writer(fake_redis, {})
    writer.start()
    thread = writer._thread
    assert thread is not None and thread.is_alive()
    writer.stop()
    assert thread.is_alive() is False
    assert writer._thread is None


def test_heartbeat_tolerates_payload_exception(fake_redis: FakeRedis):
    """A throwing payload getter must not crash the writer."""
    from amazingdata.heartbeat import HeartbeatWriter

    def bad():
        raise RuntimeError("nope")

    writer = HeartbeatWriter(
        service_name="boom",
        get_payload=bad,
        get_counter_snapshot=lambda: {},
        interval_sec=0.05,
        redis_client=fake_redis,
    )
    writer.start()
    time.sleep(0.2)
    writer.stop()
    # No heartbeat key was written — but the thread didn't crash.
    assert fake_redis.get("adshare:heartbeat:boom") is None