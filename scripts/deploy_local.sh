#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

if [ ! -f .env ]; then
    echo "==> .env file not found. Running setup script..."
    bash scripts/setup.sh
fi

echo "==> Building and launching containers with Docker Compose..."
docker compose up -d --build

echo "==> Running healthcheck probe..."
bash scripts/healthcheck.sh

echo "==> Docker Compose deployment status:"
docker compose ps

