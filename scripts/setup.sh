#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

echo "==> Verifying required CLI tools..."
REQUIRED_TOOLS=("docker" "python3" "git" "openssl")
MISSING_TOOLS=()

for tool in "${REQUIRED_TOOLS[@]}"; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        MISSING_TOOLS+=("${tool}")
    fi
done

if [ ${#MISSING_TOOLS[@]} -gt 0 ]; then
    echo "ERROR: Missing required CLI tool(s): ${MISSING_TOOLS[*]}" >&2
    echo "Please install all required tools before continuing." >&2
    exit 1
fi

echo "==> Verifying Docker Compose v2..."
if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: Docker Compose v2 is required but 'docker compose version' failed." >&2
    echo "Please ensure the Docker Compose plugin (v2) is installed." >&2
    exit 1
fi

if [ ! -f .env ]; then
    echo "==> .env file not found. Copying from .env.example..."
    cp .env.example .env

    POSTGRES_SECRET="$(openssl rand -hex 16)"
    APP_SESSION_SECRET="$(openssl rand -hex 16)"

    # Safely substitute secrets in .env
    sed -i.bak -e "s#POSTGRES_PASSWORD=.*#POSTGRES_PASSWORD=${POSTGRES_SECRET}#" \
               -e "s#DATABASE_URL=postgresql://postgres:[^@]*@db:5432/signalos#DATABASE_URL=postgresql://postgres:${POSTGRES_SECRET}@db:5432/signalos#" \
               -e "s#SESSION_SECRET=.*#SESSION_SECRET=${APP_SESSION_SECRET}#" .env
    rm -f .env.bak
    echo "==> Generated random secrets and created .env successfully."
else
    echo "==> .env file already exists. Skipping secret generation."
fi

echo "==> Setup completed successfully."

