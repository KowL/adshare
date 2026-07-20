"""Integration tests for the tushare ``rt_k`` / ``rt_min`` handlers."""

import json
from datetime import datetime, timedelta

import pytest

from adshare.core.exceptions import InvalidParameterError
from adshare.routers.tushare import realtime as realtime_mod
from adshare.services.realtime_tushare_mapper import kline_columns, quote_columns


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal Redis stand-in covering the stream read path."""

    def __init__(self, entries=None):
        self._entries = entries or []

    def xrevrange(self, key, max, min, count=None):
        if count is None:
            return list(self._entries)
        return list(self._entries[:count])


class FakeCacheManager:
    """Minimal CacheManager stand-in (snapshot key + kline stream)."""

    def __init__(self, snapshots=None, stream_entries=None):
        self._snapshots = snapshots or {}
        self.redis = FakeRedis(stream_entries)

    def _make_key(self, *parts):
        return ":".join(["adshare", *parts])

    def get_realtime_market(self, *key_parts):
        # key_parts = (REALTIME_QUOTE_KEY, code)
        return self._snapshots.get(key_parts[-1])


def _sample_snapshot(code="600000.SH") -> dict:
    payload = {
        "code": code,
        "trade_time": "2026-07-16T14:32:15",
        "pre_close": 10.30,
        "last": 10.45,
        "open": 10.32,
        "high": 10.50,
        "low": 10.28,
        "volume": 52345678,
        "amount": 547832156.32,
        "num_trades": 12345,
        "high_limited": 11.33,
        "low_limited": 9.27,
    }
    for i in range(1, 6):
        payload[f"bid_price{i}"] = 10.44 - (i - 1) * 0.01
        payload[f"ask_price{i}"] = 10.45 + (i - 1) * 0.01
        payload[f"bid_volume{i}"] = 1000 * i
        payload[f"ask_volume{i}"] = 500 * i
    return payload


def _stream_entries(code="600000.SH", n=3) -> list:
    """Build newest-first XREVRANGE-style entries (bytes, like redis-py)."""
    base = datetime(2026, 7, 16, 14, 30)
    entries = []
    for i in range(n):
        t = base + timedelta(minutes=i)
        payload = {
            "code": code,
            "kline_time": t.isoformat(),
            "open": 10.42 + i * 0.01,
            "high": 10.43 + i * 0.01,
            "low": 10.41 + i * 0.01,
            "close": 10.43 + i * 0.01,
            "volume": 12345 + i,
            "amount": 12876543.0 + i,
        }
        ms = int(t.timestamp() * 1000)
        entries.append(
            (
                f"{ms}-0".encode(),
                {
                    b"trade_time": str(ms).encode(),
                    b"data": json.dumps(payload).encode(),
                },
            )
        )
    entries.reverse()  # XREVRANGE returns newest first
    return entries


@pytest.fixture
def patch_cache(monkeypatch):
    """Patch the handler module's cache factory; returns a setter."""

    def _set(fake_cache):
        monkeypatch.setattr(realtime_mod, "get_cache_manager", lambda: fake_cache)

    return _set


# ---------------------------------------------------------------------------
# handle_rt_k
# ---------------------------------------------------------------------------


class TestHandleRtK:
    def test_single_code(self, patch_cache):
        patch_cache(FakeCacheManager(snapshots={"600000.SH": _sample_snapshot()}))
        result = realtime_mod.handle_rt_k({"ts_code": "600000.SH"}, None)
        assert result["code"] == 0
        assert result["data"]["fields"] == quote_columns()
        items = result["data"]["items"]
        assert len(items) == 1
        row = dict(zip(result["data"]["fields"], items[0]))
        assert row["ts_code"] == "600000.SH"
        assert row["trade_time"] == "2026-07-16 14:32:15"
        assert row["price"] == 10.45
        assert row["b1_p"] == 10.44
        assert row["a1_v"] == 500
        assert row["high_limit"] == 11.33
        assert row["low_limit"] == 9.27

    def test_missing_cache_returns_empty(self, patch_cache):
        patch_cache(FakeCacheManager())
        result = realtime_mod.handle_rt_k({"ts_code": "600000.SH"}, None)
        assert result["code"] == 0
        assert result["data"]["items"] == []

    def test_ts_code_required(self, patch_cache):
        patch_cache(FakeCacheManager())
        with pytest.raises(InvalidParameterError):
            realtime_mod.handle_rt_k({}, None)

    def test_handler_ignores_unified_entry_kwargs(self, patch_cache):
        patch_cache(FakeCacheManager(snapshots={"600000.SH": _sample_snapshot()}))
        result = realtime_mod.handle_rt_k(
            {"ts_code": "600000.SH"},
            None,
            service=None,
            up_service=None,
            down_service=None,
        )
        assert result["code"] == 0
        assert len(result["data"]["items"]) == 1

    def test_fields_filter(self, patch_cache):
        patch_cache(FakeCacheManager(snapshots={"600000.SH": _sample_snapshot()}))
        result = realtime_mod.handle_rt_k(
            {"ts_code": "600000.SH"}, ["ts_code", "price"]
        )
        assert result["data"]["fields"] == ["ts_code", "price"]
        assert result["data"]["items"] == [["600000.SH", 10.45]]


# ---------------------------------------------------------------------------
# handle_rt_min
# ---------------------------------------------------------------------------


class TestHandleRtMin:
    def test_recent_bars_ascending(self, patch_cache):
        patch_cache(FakeCacheManager(stream_entries=_stream_entries(n=3)))
        result = realtime_mod.handle_rt_min(
            {"ts_code": "600000.SH", "freq": "1MIN"}, None
        )
        assert result["code"] == 0
        assert result["data"]["fields"] == kline_columns()
        items = result["data"]["items"]
        assert len(items) == 3
        times = [row[1] for row in items]
        assert times == [
            "2026-07-16 14:30:00",
            "2026-07-16 14:31:00",
            "2026-07-16 14:32:00",
        ]
        first = dict(zip(result["data"]["fields"], items[0]))
        assert first["ts_code"] == "600000.SH"
        assert first["freq"] == "1MIN"
        assert first["open"] == 10.42
        assert first["vol"] == 12345

    def test_freq_case_and_period_spelling(self, patch_cache):
        patch_cache(FakeCacheManager(stream_entries=_stream_entries(n=1)))
        for freq in ("1min", "min1", "1MIN"):
            result = realtime_mod.handle_rt_min(
                {"ts_code": "600000.SH", "freq": freq}, None
            )
            assert result["code"] == 0
            assert result["data"]["items"][0][2] == "1MIN"

    def test_limit(self, patch_cache):
        patch_cache(FakeCacheManager(stream_entries=_stream_entries(n=5)))
        result = realtime_mod.handle_rt_min(
            {"ts_code": "600000.SH", "freq": "1MIN", "limit": 2}, None
        )
        items = result["data"]["items"]
        assert len(items) == 2
        # limit 取最近 N 根，再按时间升序
        assert [row[1] for row in items] == [
            "2026-07-16 14:33:00",
            "2026-07-16 14:34:00",
        ]

    def test_start_end_time_filter(self, patch_cache):
        patch_cache(FakeCacheManager(stream_entries=_stream_entries(n=3)))
        result = realtime_mod.handle_rt_min(
            {
                "ts_code": "600000.SH",
                "freq": "1MIN",
                "start_time": "2026-07-16 14:31:00",
                "end_time": "2026-07-16 14:31:59",
            },
            None,
        )
        items = result["data"]["items"]
        assert [row[1] for row in items] == ["2026-07-16 14:31:00"]

    def test_empty_stream(self, patch_cache):
        patch_cache(FakeCacheManager())
        result = realtime_mod.handle_rt_min(
            {"ts_code": "600000.SH", "freq": "1MIN"}, None
        )
        assert result["code"] == 0
        assert result["data"]["items"] == []

    def test_invalid_freq(self, patch_cache):
        patch_cache(FakeCacheManager())
        with pytest.raises(InvalidParameterError):
            realtime_mod.handle_rt_min(
                {"ts_code": "600000.SH", "freq": "3MIN"}, None
            )

    def test_freq_required(self, patch_cache):
        patch_cache(FakeCacheManager())
        with pytest.raises(InvalidParameterError):
            realtime_mod.handle_rt_min({"ts_code": "600000.SH"}, None)

    def test_single_code_only(self, patch_cache):
        patch_cache(FakeCacheManager())
        with pytest.raises(InvalidParameterError):
            realtime_mod.handle_rt_min(
                {"ts_code": "600000.SH,000001.SZ", "freq": "1MIN"}, None
            )

    def test_invalid_time_param(self, patch_cache):
        patch_cache(FakeCacheManager(stream_entries=_stream_entries(n=1)))
        with pytest.raises(InvalidParameterError):
            realtime_mod.handle_rt_min(
                {"ts_code": "600000.SH", "freq": "1MIN", "start_time": "bad"},
                None,
            )


# ---------------------------------------------------------------------------
# Unified entry + REST routes
# ---------------------------------------------------------------------------


class TestRoutes:
    def test_unified_entry_rt_k(self, client, patch_cache):
        patch_cache(FakeCacheManager(snapshots={"600000.SH": _sample_snapshot()}))
        response = client.post(
            "/tushare",
            json={
                "api_name": "rt_k",
                "token": "",
                "params": {"ts_code": "600000.SH"},
                "fields": "",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["fields"] == quote_columns()
        assert data["data"]["items"][0][0] == "600000.SH"

    def test_unified_entry_rt_min(self, client, patch_cache):
        patch_cache(FakeCacheManager(stream_entries=_stream_entries(n=2)))
        response = client.post(
            "/tushare",
            json={
                "api_name": "rt_min",
                "token": "",
                "params": {"ts_code": "600000.SH", "freq": "1MIN"},
                "fields": "",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["fields"] == kline_columns()
        assert len(data["data"]["items"]) == 2

    def test_unified_entry_invalid_freq(self, client, patch_cache):
        patch_cache(FakeCacheManager())
        response = client.post(
            "/tushare",
            json={
                "api_name": "rt_min",
                "token": "",
                "params": {"ts_code": "600000.SH", "freq": "3MIN"},
                "fields": "",
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == -1

    def test_rest_rt_k_get(self, client, patch_cache):
        patch_cache(FakeCacheManager(snapshots={"600000.SH": _sample_snapshot()}))
        response = client.get(
            "/tushare/realtime/rt_k", params={"ts_code": "600000.SH"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["items"][0][0] == "600000.SH"

    def test_rest_rt_min_get(self, client, patch_cache):
        patch_cache(FakeCacheManager(stream_entries=_stream_entries(n=3)))
        response = client.get(
            "/tushare/realtime/rt_min",
            params={"ts_code": "600000.SH", "freq": "5MIN"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["items"][0][2] == "5MIN"

    def test_rest_rt_min_post(self, client, patch_cache):
        patch_cache(FakeCacheManager(stream_entries=_stream_entries(n=1)))
        response = client.post(
            "/tushare/realtime/rt_min",
            json={"ts_code": "600000.SH", "freq": "1MIN"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert len(data["data"]["items"]) == 1

    def test_rest_rt_k_no_data(self, client, patch_cache):
        patch_cache(FakeCacheManager())
        response = client.get(
            "/tushare/realtime/rt_k", params={"ts_code": "600000.SH"}
        )
        assert response.status_code == 200
        assert response.json()["data"]["items"] == []
