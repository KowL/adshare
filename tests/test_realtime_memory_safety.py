"""Memory-safety regression tests for the AmazingData realtime worker."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from amazingdata.realtime import RealtimePublisher
from amazingdata.realtime_buffer import (
    BoundedThreadPoolExecutor,
    RealtimeEvent,
    RealtimeRedisWriter,
)


def test_sdk_executor_drops_work_instead_of_growing_without_bound():
    release = threading.Event()
    executor = BoundedThreadPoolExecutor(max_workers=1, max_pending=2)

    first = executor.submit(release.wait)
    second = executor.submit(release.wait)
    rejected = executor.submit(release.wait)

    snapshot = executor.snapshot()
    assert snapshot["outstanding"] <= 2
    assert snapshot["dropped"] == 1
    assert rejected.cancelled()

    release.set()
    first.result(timeout=1)
    second.result(timeout=1)
    executor.shutdown()


def test_realtime_buffer_coalesces_latest_event_and_evicts_oldest():
    writer = RealtimeRedisWriter(
        cache=MagicMock(),
        max_pending=2,
        batch_size=10,
        flush_interval=0.01,
    )

    writer.submit(RealtimeEvent("quote", "000001.SZ", None, {"price": 10.0}))
    writer.submit(RealtimeEvent("quote", "000001.SZ", None, {"price": 10.1}))
    writer.submit(RealtimeEvent("quote", "600000.SH", None, {"price": 8.0}))
    writer.submit(RealtimeEvent("quote", "600519.SH", None, {"price": 1500.0}))

    batch = writer._buffer.get_batch(10)
    assert [(event.code, event.data["price"]) for event in batch] == [
        ("600000.SH", 8.0),
        ("600519.SH", 1500.0),
    ]
    assert writer.snapshot()["coalesced"] == 1
    assert writer.snapshot()["dropped"] == 1


class RecordingPipeline:
    def __init__(self) -> None:
        self.commands = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.commands.append((name, args, kwargs))
            return self

        return record

    def execute(self):
        return [True] * len(self.commands)


def test_realtime_writer_batches_quote_and_kline_in_one_pipeline():
    pipeline = RecordingPipeline()
    redis = MagicMock()
    redis.pipeline.return_value = pipeline
    cache = SimpleNamespace(
        redis=redis,
        settings=SimpleNamespace(
            cache_key_prefix="adshare",
            cache_ttl_realtime=300,
        ),
        _make_key=lambda *parts: ":".join(parts),
    )
    writer = RealtimeRedisWriter(
        cache=cache,
        max_pending=10,
        batch_size=10,
        flush_interval=0.01,
        kline_history_ttl=86400,
        kline_max_bars=240,
    )

    writer._write_batch(
        [
            RealtimeEvent("quote", "000001.SZ", None, {"price": 10.1}),
            RealtimeEvent("kline", "000001.SZ", "min1", {"close": 10.1}),
        ]
    )

    names = [command[0] for command in pipeline.commands]
    assert names == [
        "setex",
        "publish",
        "setex",
        "xadd",
        "expire",
        "publish",
    ]
    redis.pipeline.assert_called_once_with(transaction=False)
    assert writer.snapshot()["batches"] == 1
    assert writer.snapshot()["written"] == 2


def test_realtime_callback_only_enqueues_and_does_not_write_redis():
    publisher = RealtimePublisher()
    publisher._writer = MagicMock()
    publisher._writer.submit.return_value = True

    publisher._handle_snapshot(
        {"code": "000001.SZ", "price": 10.1},
        period=0,
    )

    event = publisher._writer.submit.call_args.args[0]
    assert isinstance(event, RealtimeEvent)
    assert event.kind == "quote"
    assert event.code == "000001.SZ"
    assert event.data["price"] == 10.1
    assert publisher.stats["total_received"] == 1
    assert publisher.stats["enqueued"] == 1
