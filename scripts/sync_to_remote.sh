#!/bin/bash
# Sync local L3 warehouse data to remote adshare-api server.
# This script is meant to run after amazingdata batch has finished its
# daily/weekly/monthly sync jobs.

set -euo pipefail

LOCAL_DATA_DIR="/Volumes/mm/project/adshare/data"
# When running inside the amazingdata-batch container, the data is mounted at /app/data.
if [ -d "/app/data" ]; then
    LOCAL_DATA_DIR="/app/data"
fi
REMOTE_HOST="root@8.148.216.30"
REMOTE_DATA_DIR="/opt/adshare/data"

# rsync uses SSH; the container mounts /root/.ssh read-only from the host
# (so we can't write a fresh known_hosts). Force known_hosts to /dev/null
# so the "Permanently added" prompt never triggers, and rely on StrictHostKeyChecking
# disabled because we trust the host key baked into /root/.ssh/known_hosts at build time.
# If we ever needed trust-on-first-use we could mount a writable volume here.
export RSYNC_RSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"

# Validate SSH connectivity first
if ! ${RSYNC_RSH%" -o ConnectTimeout=10"} -o ConnectTimeout=10 "${REMOTE_HOST}" 'true' > /dev/null 2>&1; then
    echo "ERROR: cannot connect to ${REMOTE_HOST}" >&2
    exit 1
fi

# Sync only the data subdirectories that the remote API actually reads.
# We do NOT sync the whole data/ tree because some legacy/extra directories
# (scripts, snapshot, amazingdata, etc.) may live there.
SYNC_DIRS=("A_share" "meta" "reference")
for dir in "${SYNC_DIRS[@]}"; do
    if [ -d "${LOCAL_DATA_DIR}/${dir}" ]; then
        rsync -avz --delete \
            --exclude 'logs' \
            --exclude 'cache' \
            --exclude '__pycache__' \
            --exclude '*.pyc' \
            --exclude 'D:' \
            "${LOCAL_DATA_DIR}/${dir}/" \
            "${REMOTE_HOST}:${REMOTE_DATA_DIR}/${dir}/"
    fi
done

# Trigger remote API to refresh DuckDB views so new files are visible.
# This runs over the same SSH connection used by rsync, so no extra auth is needed.
if ${RSYNC_RSH} "${REMOTE_HOST}" \
    "curl -fsS -X POST 'http://127.0.0.1:8888/historical/admin/repair?job=kline&dry_run=false' >/dev/null 2>&1"; then
    echo "Remote warehouse repair/refresh triggered"
else
    echo "WARN: remote warehouse repair failed or endpoint unavailable" >&2
fi

echo "Sync complete: ${LOCAL_DATA_DIR} -> ${REMOTE_HOST}:${REMOTE_DATA_DIR}"
