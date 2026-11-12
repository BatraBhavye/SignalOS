#!/usr/bin/env bash
set -euo pipefail

TARGET_URL="${1:-http://127.0.0.1:8000/health}"
MAX_ATTEMPTS=10
RETRY_DELAY=2

echo "==> Polling healthcheck at ${TARGET_URL}..."

LAST_OUTPUT=""
SUCCESS=0

for ((attempt = 1; attempt <= MAX_ATTEMPTS; attempt++)); do
    echo "Attempt ${attempt}/${MAX_ATTEMPTS}..."
    if response=$(curl -fsS "${TARGET_URL}" 2>&1); then
        LAST_OUTPUT="${response}"
        if [[ "${response}" == *'"status":"ok"'* ]] || [[ "${response}" == *'"status": "ok"'* ]]; then
            echo "==> Healthcheck succeeded: ${response}"
            SUCCESS=1
            break
        else
            echo "Unexpected response received: ${response}" >&2
        fi
    else
        LAST_OUTPUT="${response}"
        echo "Probe attempt failed (service might be starting up)." >&2
    fi

    if [ "${attempt}" -lt "${MAX_ATTEMPTS}" ]; then
        sleep "${RETRY_DELAY}"
    fi
done

if [ "${SUCCESS}" -ne 1 ]; then
    echo "ERROR: Healthcheck probe failed after ${MAX_ATTEMPTS} attempts." >&2
    echo "Last captured response or error:" >&2
    echo "${LAST_OUTPUT}" >&2
    exit 1
fi

