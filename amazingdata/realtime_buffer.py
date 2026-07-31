"""Bounded execution and batched Redis output for realtime market data.

The vendor SDK dispatches every tick through an unbounded
``ThreadPoolExecutor``.  A slow callback therefore retains raw native tick
objects indefinitely.  This module puts hard bounds on both sides of the
worker:

* :class:`BoundedThreadPoolExecutor` caps SDK callback work.
* :class:`RealtimeRedisWriter` coalesces pending ticks by code/period and
  writes them with a Redis pipeline.
"""

from __future__ import annotations

import json
import pickle
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from adshare.core.logging import get_logger
from adshare.core.realtime_keys import (
    CHANNEL_INDEX,
    CHANNEL_KLINE_PREFIX,
    CHANNEL_QUOTE,
    REALTIME_INDEX_KEY,
    REALTIME_KLINE_HIST_KEY,
    REALTIME_KLINE_KEY,
    REALTIME_QUOTE_KEY,
)

logger = get_logger("amazingdata.realtime_buffer")


class BoundedThreadPoolExecutor:
    """A non-blocking executor with a hard cap on outstanding work.

    When the cap is reached, new work is cancelled instead of being appended
    to an unbounded queue.  Dropping a realtime update is preferable to
    retaining native SDK objects until the process is killed by the OOM
    killer.  The Redis writer below normally keeps callbacks fast enough that
    this is only an emergency guard.
    """

    def __init__(
        self,
        max_workers: int,
        max_pending: int,
        thread_name_prefix: str = "amazingdata-sdk",
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if max_pending < max_workers:
            raise ValueError("max_pending must be >= max_workers")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._slots = threading.BoundedSemaphore(max_pending)
        self._lock = threading.Lock()
        self._outstanding = 0
        self._submitted = 0
        self._completed = 0
        self._dropped = 0

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        if not self._slots.acquire(blocking=False):
            rejected: Future = Future()
            rejected.cancel()
            with self._lock:
                self._dropped += 1
            return rejected

        with self._lock:
            self._outstanding += 1
            self._submitted += 1
        try:
            future = self._executor.submit(fn, *args, **kwargs)
        except BaseException:
            self._release_slot()
            raise
        future.add_done_callback(lambda _future: self._release_slot())
        return future

    def _release_slot(self) -> None:
        with self._lock:
            self._outstanding -= 1
            self._completed += 1
        self._slots.release()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "outstanding": self._outstanding,
                "submitted": self._submitted,
                "completed": self._completed,
                "dropped": self._dropped,
            }

    def shutdown(
        self,
        wait: bool = True,
        *,
        cancel_futures: bool = False,
    ) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)


@dataclass(slots=True)
class RealtimeEvent:
    """A serialised market update waiting to be written to Redis."""

    kind: str
    code: str
    period: str | None
    data: dict[str, Any]
    received_at: float = field(default_factory=time.time)

    @property
    def key(self) -> tuple[str, str, str | None]:
        return (self.kind, self.code, self.period)


class _LatestEventBuffer:
    """Bounded ordered map that keeps only the latest event for each key."""

    def __init__(self, max_pending: int) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        self._max_pending = max_pending
        self._pending: OrderedDict[tuple[str, str, str | None], RealtimeEvent] = OrderedDict()
        self._condition = threading.Condition()
        self._closed = False
        self._submitted = 0
        self._coalesced = 0
        self._dropped = 0

    def put(self, event: RealtimeEvent) -> bool:
        with self._condition:
            if self._closed:
                self._dropped += 1
                return False
            self._submitted += 1
            if event.key in self._pending:
                self._pending.pop(event.key)
                self._coalesced += 1
            elif len(self._pending) >= self._max_pending:
                self._pending.popitem(last=False)
                self._dropped += 1
            self._pending[event.key] = event
            self._condition.notify()
            return True

    def get_batch(
        self,
        max_items: int,
        timeout: float = 0.0,
    ) -> list[RealtimeEvent]:
        with self._condition:
            if not self._pending and not self._closed and timeout > 0:
                self._condition.wait(timeout)
            batch: list[RealtimeEvent] = []
            while self._pending and len(batch) < max_items:
                _key, event = self._pending.popitem(last=False)
                batch.append(event)
            return batch

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def drained(self) -> bool:
        with self._condition:
            return self._closed and not self._pending

    def snapshot(self) -> dict[str, int]:
        with self._condition:
            return {
                "queue_depth": len(self._pending),
                "submitted": self._submitted,
                "coalesced": self._coalesced,
                "dropped": self._dropped,
            }


class RealtimeRedisWriter:
    """Coalesce realtime events and flush them through Redis pipelines."""

    def __init__(
        self,
        cache: Any,
        *,
        max_pending: int,
        batch_size: int,
        flush_interval: float,
        kline_history_ttl: int = 86400,
        kline_max_bars: int = 240,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if flush_interval <= 0:
            raise ValueError("flush_interval must be positive")
        self._cache = cache
        self._buffer = _LatestEventBuffer(max_pending)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._kline_history_ttl = kline_history_ttl
        self._kline_max_bars = kline_max_bars
        self._thread: threading.Thread | None = None
        self._stats_lock = threading.Lock()
        self._batches = 0
        self._written = 0
        self._write_failed = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="realtime-redis-writer",
            daemon=True,
        )
        self._thread.start()

    def submit(self, event: RealtimeEvent) -> bool:
        return self._buffer.put(event)

    def stop(self, timeout: float = 10.0) -> None:
        self._buffer.close()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("Realtime Redis writer did not flush within %.1fs", timeout)
        self._thread = None

    def snapshot(self) -> dict[str, int]:
        result = self._buffer.snapshot()
        with self._stats_lock:
            result.update(
                {
                    "batches": self._batches,
                    "written": self._written,
                    "write_failed": self._write_failed,
                }
            )
        return result

    def _run(self) -> None:
        while not self._buffer.drained():
            batch = self._buffer.get_batch(
                self._batch_size,
                timeout=self._flush_interval,
            )
            if batch:
                self._write_batch(batch)

    def _write_batch(self, batch: list[RealtimeEvent]) -> None:
        pipeline = self._cache.redis.pipeline(transaction=False)
        ttl = int(self._cache.settings.cache_ttl_realtime)
        try:
            for event in batch:
                self._append_event_commands(pipeline, event, ttl)
            pipeline.execute()
        except Exception as exc:
            with self._stats_lock:
                self._write_failed += len(batch)
            logger.error(
                "Realtime Redis pipeline failed for %s events: %s",
                len(batch),
                exc,
            )
            return
        with self._stats_lock:
            self._batches += 1
            self._written += len(batch)

    def _append_event_commands(
        self,
        pipeline: Any,
        event: RealtimeEvent,
        ttl: int,
    ) -> None:
        if event.kind == "quote":
            key = self._cache._make_key("realtime", REALTIME_QUOTE_KEY, event.code)
            channel = CHANNEL_QUOTE
        elif event.kind == "index":
            key = self._cache._make_key("realtime", REALTIME_INDEX_KEY, event.code)
            channel = CHANNEL_INDEX
        elif event.kind == "kline" and event.period:
            key = self._cache._make_key("realtime", REALTIME_KLINE_KEY, event.period, event.code)
            channel = f"{CHANNEL_KLINE_PREFIX}{event.period}"
        else:
            raise ValueError(f"Unsupported realtime event: {event.kind!r}")

        pipeline.setex(key, ttl, pickle.dumps(event.data))

        if event.kind == "kline":
            stream_key = self._cache._make_key(
                "realtime",
                f"{REALTIME_KLINE_HIST_KEY}:{event.period}",
                event.code,
            )
            pipeline.xadd(
                stream_key,
                {
                    "trade_time": self._kline_time_ms(event.data),
                    "data": json.dumps(event.data),
                },
                maxlen=self._kline_max_bars,
                approximate=True,
            )
            pipeline.expire(stream_key, self._kline_history_ttl)

        message = json.dumps(
            {
                "type": event.kind,
                "code": event.code,
                **({"period": event.period} if event.period else {}),
                "data": event.data,
                "timestamp": datetime.fromtimestamp(event.received_at).isoformat(),
            }
        )
        pipeline.publish(channel, message)

    @staticmethod
    def _kline_time_ms(serialized: dict[str, Any]) -> int:
        raw = serialized.get("kline_time") or serialized.get("trade_time")
        if raw:
            try:
                return int(datetime.fromisoformat(str(raw)).timestamp() * 1000)
            except (ValueError, TypeError, OverflowError):
                pass
        return int(time.time() * 1000)
