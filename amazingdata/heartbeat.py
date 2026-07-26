"""Heartbeat + counter-history writer shared by amazingdata containers.

Each container runs a ``HeartbeatWriter`` daemon thread that:

* Writes a JSON heartbeat to ``adshare:heartbeat:<service>`` every
  ``interval_sec`` seconds with a 30 s TTL. The TTL is what the
  dashboard reads as liveness — if the key expires, the container is
  presumed dead.
* Snapshots the counters to ``adshare:stats:<service>`` (Redis Stream
  with ``MAXLEN ~ 30``) once per minute. The dashboard's sparkline
  reads the last 30 entries.

Raw ``cache.redis`` is used for both writes (not ``cache.set`` which
pickles) so the values are human-readable in ``redis-cli`` and avoid
the deserialisation-attack surface of pickle.

The writer is intentionally simple: the only "smart" part is the
1-minute wall-clock alignment for the snapshot, so two consecutive
heartbeats in the same second do not produce two snapshots.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, Optional


_HEARTBEAT_TTL_SEC = 30
_HISTORY_INTERVAL_SEC = 60
_HISTORY_MAXLEN = 30


class HeartbeatWriter:
    """Daemon thread that publishes heartbeat + counter snapshots to Redis.

    Parameters
    ----------
    service_name:
        Identifier used in both the heartbeat key and stats stream
        (e.g. ``"amazingdata-realtime"``).
    get_payload:
        Callable returning the JSON-serialisable dict to write as the
        heartbeat. Should be cheap; called every tick.
    get_counter_snapshot:
        Callable returning the counter dict to append to the history
        stream. May be empty. Called once per minute.
    interval_sec:
        Heartbeat tick interval. Defaults to 10 s.
    redis_client:
        The Redis client. Defaults to ``get_cache_manager().redis`` and
        is captured at construction time so a worker that swaps the
        cache manager after startup still gets the right one.
    """

    def __init__(
        self,
        service_name: str,
        get_payload: Callable[[], Dict[str, Any]],
        get_counter_snapshot: Callable[[], Dict[str, Any]],
        interval_sec: float = 10.0,
        redis_client: Optional[Any] = None,
    ) -> None:
        self._service_name = service_name
        self._get_payload = get_payload
        self._get_counter_snapshot = get_counter_snapshot
        self._interval = float(interval_sec)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_history_ts: float = 0.0
        self._redis = redis_client
        self._owns_redis = redis_client is None

    @property
    def service_name(self) -> str:
        return self._service_name

    def _ensure_redis(self) -> Any:
        if self._redis is None:
            from adshare.core.cache import get_cache_manager

            self._redis = get_cache_manager().redis
        return self._redis

    def start(self) -> None:
        """Start the daemon thread. Returns immediately."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"heartbeat-{self._service_name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Signal stop and wait briefly for the thread to exit."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def _run(self) -> None:
        # Initialise lazily so importing this module never touches Redis.
        redis = self._ensure_redis()
        heartbeat_key = f"adshare:heartbeat:{self._service_name}"
        history_stream = f"adshare:stats:{self._service_name}"

        while not self._stop_event.is_set():
            tick_started = time.time()
            try:
                payload = self._get_payload()
                payload.setdefault("ts", tick_started)
                redis.set(
                    heartbeat_key,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    ex=_HEARTBEAT_TTL_SEC,
                )
            except Exception:
                # Heartbeat failures must not crash the worker. The TTL
                # will expire and the dashboard will see "down" — which
                # is the right outcome.
                pass

            try:
                if tick_started - self._last_history_ts >= _HISTORY_INTERVAL_SEC:
                    snapshot = self._get_counter_snapshot()
                    snapshot.setdefault("ts", tick_started)
                    redis.xadd(
                        history_stream,
                        {"data": json.dumps(snapshot, ensure_ascii=False, default=str)},
                        maxlen=_HISTORY_MAXLEN,
                        approximate=True,
                    )
                    self._last_history_ts = tick_started
            except Exception:
                pass

            self._stop_event.wait(self._interval)