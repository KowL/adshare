#!/usr/bin/env python3
"""Health check script for HTTP endpoints."""

import argparse
import contextlib
import http.server
import io
import json
import socket
import sys
import threading
import time
import urllib.request
import urllib.error


def main():
    parser = argparse.ArgumentParser(description="HTTP health check script")
    parser.add_argument("--url", default="http://localhost:8888/health",
                        help="Health check URL (default: http://localhost:8888/health)")
    parser.add_argument("--timeout", type=int, default=5,
                        help="Request timeout in seconds (default: 5)")
    args = parser.parse_args()

    start = time.perf_counter()
    try:
        req = urllib.request.Request(args.url)
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            status_code = resp.status
            elapsed_ms = (time.perf_counter() - start) * 1000
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        result = {
            "status": "error",
            "url": args.url,
            "status_code": e.code,
            "response_time_ms": round(elapsed_ms, 2),
            "message": f"HTTP error: {e.reason}"
        }
        print(json.dumps(result))
        return
    except urllib.error.URLError as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        result = {
            "status": "error",
            "url": args.url,
            "status_code": 0,
            "response_time_ms": round(elapsed_ms, 2),
            "message": f"Connection error: {e.reason}"
        }
        print(json.dumps(result))
        return
    except (TimeoutError, socket.timeout):
        elapsed_ms = (time.perf_counter() - start) * 1000
        result = {
            "status": "error",
            "url": args.url,
            "status_code": None,
            "response_time_ms": round(elapsed_ms, 2),
            "message": "Request timed out"
        }
        print(json.dumps(result))
        return

    if status_code == 200:
        status = "ok"
        message = "healthy"
    else:
        status = "error"
        message = f"Unexpected status code: {status_code}"

    result = {
        "status": status,
        "url": args.url,
        "status_code": status_code,
        "response_time_ms": round(elapsed_ms, 2),
        "message": message
    }
    print(json.dumps(result))


if __name__ == "__main__":
    if "--self-test-timeout" in sys.argv:
        class SlowHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(1.1)
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            output = io.StringIO()
            sys.argv = [__file__, "--url", f"http://127.0.0.1:{server.server_port}", "--timeout", "1"]
            with contextlib.redirect_stdout(output):
                main()
            server.shutdown()
        result = json.loads(output.getvalue())
        assert result["status"] == "error"
        assert result["message"] == "Request timed out"
    else:
        main()
