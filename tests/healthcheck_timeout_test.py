"""Tests for scripts/healthcheck.py timeout handling.

These tests verify that the healthcheck script emits a clean JSON error when a
request exceeds ``--timeout`` or when the target refuses the connection. Before
the timeout fix, a slow response surfaced as an uncaught ``TimeoutError``
traceback on stderr; these assertions fail on that pre-fix behaviour and pass on
the fixed behaviour.
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import threading
import time

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEALTHCHECK_SCRIPT = os.path.join(REPO_ROOT, "scripts", "healthcheck.py")


def _run_healthcheck(*extra_args: str) -> subprocess.CompletedProcess:
    """Run the healthcheck script and return the completed process."""
    return subprocess.run(
        ["python", HEALTHCHECK_SCRIPT, *extra_args],
        capture_output=True,
        text=True,
        check=False,
    )


class _SlowRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that intentionally sleeps longer than the client timeout."""

    def do_GET(self) -> None:
        time.sleep(3)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: ARG002
        """Suppress request logs to keep test output clean."""
        return


class _FastRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that responds immediately for control-case use."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: ARG002
        return


@pytest.fixture
def ephemeral_server():
    """Yield a ``(host, port)`` tuple for a threaded HTTP server."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SlowRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "127.0.0.1", server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def unreachable_port() -> int:
    """Return a high ephemeral port that is very likely closed."""
    return 65432


def _assert_timeout_json(result: subprocess.CompletedProcess, url: str) -> None:
    """Common assertions for the timeout error JSON path."""
    assert result.returncode != 0, "healthcheck should exit non-zero on timeout"
    assert "Traceback" not in result.stderr, f"stderr contains traceback:\n{result.stderr}"
    assert "timeout" not in result.stderr.lower(), f"stderr contains timeout noise:\n{result.stderr}"

    output = json.loads(result.stdout)
    assert output.keys() == {"status", "url", "status_code", "response_time_ms", "message"}
    assert output["status"] == "error"
    assert output["url"] == url
    assert output["status_code"] is None
    assert output["message"] == "Request timed out"
    assert isinstance(output["response_time_ms"], float)


def test_timeout_slow_response_emits_clean_json_error(ephemeral_server) -> None:
    """A server that sleeps past --timeout produces a clean JSON timeout error."""
    host, port = ephemeral_server
    url = f"http://{host}:{port}/"

    result = _run_healthcheck("--url", url, "--timeout", "1")

    _assert_timeout_json(result, url)

    # The timeout should be honoured well before the server would ever respond.
    elapsed = result.stderr or ""
    # ``response_time_ms`` is the elapsed time recorded by the script; it should
    # be comfortably under the 3s server sleep.
    assert json.loads(result.stdout)["response_time_ms"] < 2000


def test_connection_refused_error_json_unchanged(unreachable_port) -> None:
    """Connection refused continues to follow the existing URLError JSON path."""
    url = f"http://127.0.0.1:{unreachable_port}/"

    result = _run_healthcheck("--url", url, "--timeout", "1")

    assert result.returncode != 0, "healthcheck should exit non-zero on connection refused"
    assert "Traceback" not in result.stderr, f"stderr contains traceback:\n{result.stderr}"

    output = json.loads(result.stdout)
    assert output.keys() == {"status", "url", "status_code", "response_time_ms", "message"}
    assert output["status"] == "error"
    assert output["url"] == url
    assert output["status_code"] == 0
    assert "Connection error" in output["message"]


def test_fast_response_still_healthy() -> None:
    """Sanity check that a quick response still returns the healthy JSON path."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FastRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = "127.0.0.1", server.server_address[1]
        url = f"http://{host}:{port}/"
        result = _run_healthcheck("--url", url, "--timeout", "5")
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 0, f"healthcheck failed unexpectedly: {result.stderr}"
    output = json.loads(result.stdout)
    assert output["status"] == "ok"
    assert output["message"] == "healthy"
    assert output["status_code"] == 200
