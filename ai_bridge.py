from __future__ import annotations

import hmac
import json
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

import server


BRIDGE_TOKEN = os.getenv("AI_BRIDGE_TOKEN", "")
MIN_TOKEN_LENGTH = 32
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("AI_BRIDGE_RATE_LIMIT", "30") or "30")
RATE_LIMITS: dict[str, deque[float]] = defaultdict(deque)

app = FastAPI(
    title="SignalOS AI Bridge",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class BridgeExtractRequest(BaseModel):
    text: str = Field(..., min_length=1)
    now: str | None = None
    source_type: str = "text"


def require_bridge_token(request: Request) -> None:
    check_rate_limit(request)
    if len(BRIDGE_TOKEN) < MIN_TOKEN_LENGTH:
        raise HTTPException(status_code=500, detail="AI bridge token must be at least 32 characters")
    supplied = request.headers.get("x-signalos-bridge-token", "")
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        supplied = authorization.split(" ", 1)[1].strip()
    if not supplied or not hmac.compare_digest(supplied, BRIDGE_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid bridge token")


def client_key(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def check_rate_limit(request: Request) -> None:
    now = time.time()
    key = client_key(request)
    hits = RATE_LIMITS[key]
    while hits and now - hits[0] > RATE_LIMIT_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="Too many bridge requests")
    hits.append(now)


def ollama_is_online() -> bool:
    try:
        req = urllib.request.Request(f"{server.OLLAMA_BASE_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


@app.get("/api/bridge/health")
def bridge_health(request: Request) -> dict[str, Any]:
    require_bridge_token(request)
    return {
        "status": "online",
        "ollama_online": ollama_is_online(),
        "model": server.OLLAMA_MODEL,
    }


@app.post("/api/bridge/extract")
def bridge_extract(payload: BridgeExtractRequest, request: Request) -> dict[str, Any]:
    require_bridge_token(request)
    now = server.parse_now(payload.now)
    try:
        candidates = server.ollama_extract(payload.text, now, payload.source_type)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Ollama unavailable") from exc
    return {
        "status": "ok",
        "model": server.OLLAMA_MODEL,
        "candidates": candidates,
    }
