from __future__ import annotations

import json
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "signalos.db"
IST_OFFSET = "+05:30"

app = FastAPI(title="SignalOS MVP", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class AnalyzeRequest(BaseModel):
    text: str = Field(..., min_length=1)
    use_ollama: bool = False
    now: str | None = None


class ReminderIn(BaseModel):
    title: str
    reminder_type: str = "reminder"
    due_at: str | None = None
    priority: str = "medium"
    confidence: float = 0.0
    source_text: str = ""


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                reminder_type TEXT NOT NULL,
                due_at TEXT,
                priority TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'scheduled',
                created_at TEXT NOT NULL
            )
            """
        )


def parse_now(raw_now: str | None) -> datetime:
    if not raw_now:
        return datetime.now()
    try:
        return datetime.fromisoformat(raw_now.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return datetime.now()


def normalize_text(text: str) -> str:
    cleaned = text.strip()
    replacements = {
        r"\bcll\b": "call",
        r"\bpls\b": "please",
        r"\bplz\b": "please",
        r"\btmrw\b": "tomorrow",
        r"\btom\b": "tomorrow",
        r"\btdy\b": "today",
        r"\bmsg\b": "message",
        r"\bassgn\b": "assignment",
        r"\bsub\b": "submit",
        r"\beod\b": "end of day",
    }
    for pattern, repl in replacements.items():
        cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def infer_type(text: str) -> str:
    lower = text.lower()
    if re.search(r"\b(meeting|class|appointment|interview|session|event)\b", lower):
        return "event"
    if re.search(r"\b(submit|finish|complete|send|pay|fill|upload|apply|book)\b", lower):
        return "task"
    if re.search(r"\b(call|message|text|remind|ping|wake|gym|chalna|jana|go|visit)\b", lower):
        return "reminder"
    return "note"


def priority(text: str, due_at: datetime | None, now: datetime) -> str:
    lower = text.lower()
    if re.search(r"\b(urgent|asap|immediately|last date|deadline|important)\b", lower):
        return "high"
    if due_at and due_at <= now + timedelta(hours=6):
        return "high"
    if due_at and due_at <= now + timedelta(days=2):
        return "medium"
    return "low"


def parse_date(text: str, now: datetime) -> datetime:
    lower = text.lower()
    date_base = now
    if "day after tomorrow" in lower:
        date_base = now + timedelta(days=2)
    elif re.search(r"\btomorrow\b|\bkal\b", lower):
        date_base = now + timedelta(days=1)
    elif re.search(r"\btoday\b|\baaj\b", lower):
        date_base = now
    else:
        weekdays = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        for name, idx in weekdays.items():
            if re.search(rf"\b(next\s+)?{name}\b", lower):
                days = (idx - now.weekday()) % 7
                if days == 0 or f"next {name}" in lower:
                    days += 7
                date_base = now + timedelta(days=days)
                break

    month_map = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    date_match = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
        lower,
    )
    if date_match:
        day = int(date_match.group(1))
        month = month_map[date_match.group(2)]
        year = now.year
        candidate = datetime(year, month, day, now.hour, now.minute)
        if candidate.date() < now.date():
            candidate = candidate.replace(year=year + 1)
        date_base = candidate

    slash_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", lower)
    if slash_match:
        day = int(slash_match.group(1))
        month = int(slash_match.group(2))
        year = int(slash_match.group(3) or now.year)
        if year < 100:
            year += 2000
        try:
            date_base = datetime(year, month, day, now.hour, now.minute)
        except ValueError:
            pass
    return date_base


def parse_time(text: str, date_base: datetime, now: datetime) -> datetime | None:
    lower = text.lower()
    time_match = re.search(r"\b(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b", lower)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        meridiem = time_match.group(3).replace(".", "")
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        due = date_base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due < now and date_base.date() == now.date():
            due += timedelta(days=1)
        return due

    baje_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*baje\b", lower)
    if baje_match:
        hour = int(baje_match.group(1))
        minute = int(baje_match.group(2) or 0)
        if hour < 8:
            hour += 12
        due = date_base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due < now and date_base.date() == now.date():
            due += timedelta(days=1)
        return due

    if "morning" in lower:
        return date_base.replace(hour=9, minute=0, second=0, microsecond=0)
    if "afternoon" in lower:
        return date_base.replace(hour=14, minute=0, second=0, microsecond=0)
    if "evening" in lower:
        return date_base.replace(hour=18, minute=0, second=0, microsecond=0)
    if "tonight" in lower or "night" in lower:
        return date_base.replace(hour=21, minute=0, second=0, microsecond=0)
    if "end of day" in lower:
        return date_base.replace(hour=20, minute=0, second=0, microsecond=0)
    return None


def title_from_text(text: str, reminder_type: str) -> str:
    lower = text.lower()
    patterns = [
        r"\bremind me to\s+(.+)",
        r"\bremember to\s+(.+)",
        r"\bdon'?t forget to\s+(.+)",
        r"\bneed to\s+(.+)",
        r"\bhave to\s+(.+)",
        r"\b(call|message|text|submit|finish|complete|send|pay|fill|upload|apply|book)\s+(.+)",
    ]
    title = text
    for pattern in patterns:
        match = re.search(pattern, lower, flags=re.IGNORECASE)
        if match:
            if len(match.groups()) == 2:
                title = f"{match.group(1)} {match.group(2)}"
            else:
                title = match.group(1)
            break
    title = re.sub(r"^\s*(?:kal|aaj|today|tomorrow)\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.|baje)\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(at|by|before|on)\s+.*$", "", title, flags=re.IGNORECASE).strip(" .,!?:;")
    title = re.sub(r"\b(today|tomorrow|tonight|kal|aaj|morning|evening|night)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" .,!?:;")
    if not title:
        title = "Review captured note" if reminder_type == "note" else "New reminder"
    return title[:1].upper() + title[1:]


def rule_extract(text: str, now: datetime) -> list[dict[str, Any]]:
    normalized = normalize_text(text)
    chunks = [chunk.strip() for chunk in re.split(r"[\n;]+|(?:\s+\band\b\s+)", normalized) if chunk.strip()]
    candidates = []
    for chunk in chunks:
        reminder_type = infer_type(chunk)
        date_base = parse_date(chunk, now)
        due = parse_time(chunk, date_base, now)
        has_action = reminder_type != "note" or due is not None or re.search(
            r"\b(deadline|last date|due|remind|task)\b", chunk, re.IGNORECASE
        )
        if not has_action:
            continue
        if not due and reminder_type in {"task", "reminder", "event"}:
            if re.search(r"\b(today|aaj)\b", chunk, re.IGNORECASE):
                due = date_base.replace(hour=20, minute=0, second=0, microsecond=0)
            elif re.search(r"\b(tomorrow|kal)\b", chunk, re.IGNORECASE):
                due = date_base.replace(hour=9, minute=0, second=0, microsecond=0)
        confidence = 0.62
        if due:
            confidence += 0.18
        if reminder_type != "note":
            confidence += 0.12
        if len(chunk.split()) <= 12:
            confidence += 0.04
        candidates.append(
            {
                "id": str(uuid4()),
                "title": title_from_text(chunk, reminder_type),
                "reminder_type": reminder_type,
                "due_at": due.isoformat(timespec="minutes") + IST_OFFSET if due else None,
                "priority": priority(chunk, due, now),
                "confidence": round(min(confidence, 0.96), 2),
                "source_text": chunk,
                "needs_confirmation": True,
                "extractor": "rules",
            }
        )
    return candidates


def ollama_extract(text: str, now: datetime) -> list[dict[str, Any]]:
    prompt = f"""
Extract actionable reminders/tasks/events from the message below.
Current local datetime: {now.isoformat(timespec="minutes")} {IST_OFFSET}.
Return only JSON with this shape:
{{"items":[{{"title":"string","reminder_type":"reminder|task|event","due_at":"ISO datetime or null","priority":"low|medium|high","confidence":0.0,"source_text":"string"}}]}}
Message: {text}
"""
    payload = json.dumps(
        {
            "model": "llama3.1:8b",
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as response:
        raw = json.loads(response.read().decode("utf-8")).get("response", "{}")
    start = raw.find("{")
    end = raw.rfind("}") + 1
    parsed = json.loads(raw[start:end])
    items = parsed.get("items", [])
    cleaned = []
    for item in items:
        cleaned.append(
            {
                "id": str(uuid4()),
                "title": str(item.get("title") or "New reminder"),
                "reminder_type": item.get("reminder_type") or "reminder",
                "due_at": item.get("due_at"),
                "priority": item.get("priority") or "medium",
                "confidence": float(item.get("confidence") or 0.7),
                "source_text": item.get("source_text") or text,
                "needs_confirmation": True,
                "extractor": "ollama",
            }
        )
    return cleaned


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/api/analyze")
def analyze(payload: AnalyzeRequest) -> dict[str, Any]:
    now = parse_now(payload.now)
    normalized = normalize_text(payload.text)
    extractor = "rules"
    candidates = []
    ollama_error = None
    if payload.use_ollama:
        try:
            candidates = ollama_extract(normalized, now)
            extractor = "ollama"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            ollama_error = str(exc)
    if not candidates:
        candidates = rule_extract(normalized, now)
    return {
        "normalized_text": normalized,
        "extractor": extractor if candidates and candidates[0].get("extractor") == "ollama" else "rules",
        "ollama_error": ollama_error,
        "candidates": candidates,
    }


@app.post("/api/reminders")
def create_reminder(payload: ReminderIn) -> dict[str, Any]:
    reminder_id = str(uuid4())
    created_at = datetime.now().isoformat(timespec="seconds") + IST_OFFSET
    with db() as conn:
        conn.execute(
            """
            INSERT INTO reminders
            (id, title, reminder_type, due_at, priority, confidence, source_text, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled', ?)
            """,
            (
                reminder_id,
                payload.title,
                payload.reminder_type,
                payload.due_at,
                payload.priority,
                payload.confidence,
                payload.source_text,
                created_at,
            ),
        )
    return {"id": reminder_id, **payload.model_dump(), "status": "scheduled", "created_at": created_at}


@app.get("/api/reminders")
def list_reminders() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM reminders ORDER BY COALESCE(due_at, created_at)").fetchall()
    return [dict(row) for row in rows]


@app.delete("/api/reminders/{reminder_id}")
def delete_reminder(reminder_id: str) -> dict[str, str]:
    with db() as conn:
        result = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return {"status": "deleted"}

