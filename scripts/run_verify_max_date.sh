#!/usr/bin/env bash
set -euo pipefail
cd /app
PYTHONPATH=/app python3 scripts/cron_verify_max_date.py
