from __future__ import annotations

import hmac
import json
import os
import urllib.error
import urllib.request
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

import server


BRIDGE_TOKEN = os.getenv("AI_BRIDGE_TOKEN", "")

app = FastAPI(title="SignalOS AI Bridge", version="0.1.0")


class BridgeExtractRequest(BaseModel):
    text: str = Field(..., min_length=1)
    now: str | None = None
    source_type: str = "text"


def require_bridge_token(request: Request) -> None:
    if not BRIDGE_TOKEN:
        raise HTTPException(status_code=500, detail="AI_BRIDGE_TOKEN is not configured on the bridge")
    supplied = request.headers.get("x-signalos-bridge-token", "")
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        supplied = authorization.split(" ", 1)[1].strip()
    if not supplied or not hmac.compare_digest(supplied, BRIDGE_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid bridge token")


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
        raise HTTPException(status_code=503, detail=f"Ollama unavailable: {exc}") from exc
    return {
        "status": "ok",
        "model": server.OLLAMA_MODEL,
        "candidates": candidates,
    }
