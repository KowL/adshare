#!/usr/bin/env bash
set -euo pipefail
cd /app
PYTHONPATH=/app python3 scripts/cron_sync_kline_daily.py
