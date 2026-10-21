from __future__ import annotations

import json
import re
import socket
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "signalos.db"
IST_OFFSET = "+05:30"

ReminderType = Literal["reminder", "task", "event", "important_date", "note"]
Priority = Literal["low", "medium", "high"]
RecurrenceKind = Literal["none", "daily", "weekly", "monthly", "yearly"]
Status = Literal["scheduled", "completed", "dismissed"]

app = FastAPI(title="SignalOS MVP", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class Recurrence(BaseModel):
    kind: RecurrenceKind = "none"
    interval: int = Field(default=1, ge=1, le=365)
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    month: int | None = Field(default=None, ge=1, le=12)


class AnalyzeRequest(BaseModel):
    text: str = Field(..., min_length=1)
    use_ollama: bool = True
    now: str | None = None
    source_type: str = "text"


class ReminderIn(BaseModel):
    title: str = Field(..., min_length=1)
    reminder_type: ReminderType = "reminder"
    due_at: str | None = None
    priority: Priority = "medium"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_text: str = ""
    source_type: str = "text"
    extractor: str = "manual"
    source_reason: str = ""
    recurrence: Recurrence = Field(default_factory=Recurrence)
    lead_minutes: int = Field(default=0, ge=0)


class ReminderPatch(BaseModel):
    status: Status | None = None


class WebhookCapture(BaseModel):
    text: str = Field(..., min_length=1)
    source_type: str = "iphone_shortcut"
    now: str | None = None


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(reminders)").fetchall()}
    columns = {
        "source_type": "TEXT NOT NULL DEFAULT 'text'",
        "extractor": "TEXT NOT NULL DEFAULT 'manual'",
        "source_reason": "TEXT NOT NULL DEFAULT ''",
        "recurrence_kind": "TEXT NOT NULL DEFAULT 'none'",
        "recurrence_interval": "INTEGER NOT NULL DEFAULT 1",
        "recurrence_day_of_month": "INTEGER",
        "recurrence_day_of_week": "INTEGER",
        "recurrence_month": "INTEGER",
        "lead_minutes": "INTEGER NOT NULL DEFAULT 0",
        "calendar_provider": "TEXT NOT NULL DEFAULT 'ics'",
        "calendar_event_id": "TEXT",
        "calendar_synced_at": "TEXT",
        "calendar_sync_status": "TEXT NOT NULL DEFAULT 'not_synced'",
        "updated_at": "TEXT",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE reminders ADD COLUMN {name} {definition}")


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
                source_type TEXT NOT NULL DEFAULT 'text',
                extractor TEXT NOT NULL DEFAULT 'manual',
                source_reason TEXT NOT NULL DEFAULT '',
                recurrence_kind TEXT NOT NULL DEFAULT 'none',
                recurrence_interval INTEGER NOT NULL DEFAULT 1,
                recurrence_day_of_month INTEGER,
                recurrence_day_of_week INTEGER,
                recurrence_month INTEGER,
                lead_minutes INTEGER NOT NULL DEFAULT 0,
                calendar_provider TEXT NOT NULL DEFAULT 'ics',
                calendar_event_id TEXT,
                calendar_synced_at TEXT,
                calendar_sync_status TEXT NOT NULL DEFAULT 'not_synced',
                status TEXT NOT NULL DEFAULT 'scheduled',
                created_at TEXT NOT NULL,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_candidates (
                id TEXT PRIMARY KEY,
                candidate_json TEXT NOT NULL,
                source_text TEXT NOT NULL,
                source_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(conn)


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
        r"\bth\b": "the",
    }
    for pattern, repl in replacements.items():
        cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def infer_type(text: str) -> ReminderType:
    lower = text.lower()
    if re.search(r"\b(anniversary|birthday|bday|birth day)\b", lower):
        return "important_date"
    if re.search(r"\b(meeting|class|appointment|interview|session|event|webinar)\b", lower):
        return "event"
    if re.search(r"\b(submit|finish|complete|send|pay|fill|upload|apply|book)\b", lower):
        return "task"
    if re.search(r"\b(call|message|text|remind|ping|wake|gym|chalna|jana|go|visit)\b", lower):
        return "reminder"
    return "note"


def priority(text: str, due_at: datetime | None, now: datetime) -> Priority:
    lower = text.lower()
    if re.search(r"\b(urgent|asap|immediately|last date|deadline|important|critical)\b", lower):
        return "high"
    if due_at and due_at <= now + timedelta(hours=6):
        return "high"
    if due_at and due_at <= now + timedelta(days=2):
        return "medium"
    return "low"


def parse_day_number(text: str) -> int | None:
    match = re.search(r"\b(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b", text.lower())
    if not match:
        return None
    day = int(match.group(1))
    if 1 <= day <= 31:
        return day
    return None


def parse_recurrence(text: str, now: datetime) -> Recurrence:
    lower = text.lower()
    weekday_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    if re.search(r"\b(every day|daily|each day)\b", lower):
        return Recurrence(kind="daily")
    if re.search(r"\b(every month|monthly|each month)\b", lower):
        return Recurrence(kind="monthly", day_of_month=parse_day_number(lower) or now.day)
    if re.search(r"\b(every year|yearly|annually|each year)\b", lower):
        return Recurrence(kind="yearly", day_of_month=parse_day_number(lower), month=now.month)
    for name, idx in weekday_map.items():
        if re.search(rf"\bevery\s+{name}\b", lower):
            return Recurrence(kind="weekly", day_of_week=idx)
    if re.search(r"\b(anniversary|birthday|bday)\b", lower) and parse_day_number(lower):
        if "every month" in lower or "monthly" in lower:
            return Recurrence(kind="monthly", day_of_month=parse_day_number(lower))
        return Recurrence(kind="yearly", day_of_month=parse_day_number(lower), month=now.month)
    return Recurrence()


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


def next_recurrence_due(recurrence: Recurrence, now: datetime) -> datetime | None:
    if recurrence.kind == "none":
        return None
    if recurrence.kind == "daily":
        return now.replace(hour=9, minute=0, second=0, microsecond=0)
    if recurrence.kind == "weekly":
        day = recurrence.day_of_week if recurrence.day_of_week is not None else now.weekday()
        days = (day - now.weekday()) % 7
        candidate = (now + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)
        if candidate < now:
            candidate += timedelta(days=7)
        return candidate
    if recurrence.kind == "monthly":
        day = recurrence.day_of_month or now.day
        month = now.month
        year = now.year
        for _ in range(14):
            try:
                candidate = datetime(year, month, day, 9, 0)
                if candidate >= now:
                    return candidate
            except ValueError:
                pass
            month += 1
            if month > 12:
                month = 1
                year += 1
    if recurrence.kind == "yearly":
        day = recurrence.day_of_month or now.day
        month = recurrence.month or now.month
        for year in (now.year, now.year + 1):
            try:
                candidate = datetime(year, month, day, 9, 0)
                if candidate >= now:
                    return candidate
            except ValueError:
                pass
    return None


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


def polish_title(title: str) -> str:
    title = title[:1].upper() + title[1:]
    acronyms = {
        "ai": "AI",
        "api": "API",
        "nlp": "NLP",
        "ocr": "OCR",
        "os": "OS",
        "ui": "UI",
    }
    for word, replacement in acronyms.items():
        title = re.sub(rf"\b{word}\b", replacement, title, flags=re.IGNORECASE)
    return title


def title_from_text(text: str, reminder_type: ReminderType) -> str:
    lower = text.lower()
    if re.search(r"\bpay\b", lower) and re.search(r"\b(fee|fees)\b", lower):
        return "Pay fees"
    if reminder_type == "important_date":
        if "anniversary" in lower:
            return "Anniversary"
        if re.search(r"\b(birthday|bday)\b", lower):
            return "Birthday"
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
    title = re.sub(
        r"\b(today|tomorrow|tonight|kal|aaj|morning|evening|night|every month|monthly|every year|yearly)\b",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\s+", " ", title).strip(" .,!?:;")
    if not title:
        title = "Review captured note" if reminder_type == "note" else "New reminder"
    return polish_title(title)


def recurrence_reason(recurrence: Recurrence) -> str:
    if recurrence.kind == "monthly" and recurrence.day_of_month:
        return f"Detected recurring monthly date on day {recurrence.day_of_month}."
    if recurrence.kind == "yearly":
        return "Detected recurring yearly important date."
    if recurrence.kind == "weekly":
        return "Detected weekly recurring reminder."
    if recurrence.kind == "daily":
        return "Detected daily recurring reminder."
    return ""


def due_to_wire(due: datetime | None) -> str | None:
    if not due:
        return None
    return due.isoformat(timespec="minutes") + IST_OFFSET


def rule_extract(text: str, now: datetime, source_type: str = "text") -> list[dict[str, Any]]:
    normalized = normalize_text(text)
    chunks = [chunk.strip() for chunk in re.split(r"[\n;]+|(?:\s+\band\s+)", normalized) if chunk.strip()]
    candidates = []
    for chunk in chunks:
        reminder_type = infer_type(chunk)
        recurrence = parse_recurrence(chunk, now)
        date_base = parse_date(chunk, now)
        due = parse_time(chunk, date_base, now) or next_recurrence_due(recurrence, now)
        has_action = reminder_type != "note" or due is not None or re.search(
            r"\b(deadline|last date|due|remind|task|important)\b", chunk, re.IGNORECASE
        )
        if not has_action:
            continue
        if not due and reminder_type in {"task", "reminder", "event"}:
            if re.search(r"\b(today|aaj)\b", chunk, re.IGNORECASE):
                due = date_base.replace(hour=20, minute=0, second=0, microsecond=0)
            elif re.search(r"\b(tomorrow|kal)\b", chunk, re.IGNORECASE):
                due = date_base.replace(hour=9, minute=0, second=0, microsecond=0)
        confidence = 0.58
        if due:
            confidence += 0.18
        if recurrence.kind != "none":
            confidence += 0.14
        if reminder_type != "note":
            confidence += 0.1
        if len(chunk.split()) <= 12:
            confidence += 0.04
        reason = recurrence_reason(recurrence) or "Detected action and time signals with the rules engine."
        candidates.append(
            {
                "id": str(uuid4()),
                "title": title_from_text(chunk, reminder_type),
                "reminder_type": reminder_type,
                "due_at": due_to_wire(due),
                "priority": priority(chunk, due, now),
                "confidence": round(min(confidence, 0.96), 2),
                "source_text": chunk,
                "source_type": source_type,
                "needs_confirmation": True,
                "extractor": "rules",
                "source_reason": reason,
                "recurrence": recurrence.model_dump(),
                "lead_minutes": 0,
            }
        )
    return candidates


def ollama_extract(text: str, now: datetime, source_type: str = "text") -> list[dict[str, Any]]:
    prompt = f"""
You are SignalOS. Extract action items from this message.
Now: {now.isoformat(timespec="minutes")} {IST_OFFSET}
Message: {text}

Return compact JSON only:
{{"items":[{{"title":"","reminder_type":"reminder","due_at":null,"priority":"medium","confidence":0.8,"source_text":"","source_reason":"","recurrence":{{"kind":"none","interval":1,"day_of_month":null,"day_of_week":null,"month":null}}}}]}}
Allowed reminder_type: reminder, task, event, important_date, note.
Allowed recurrence kind: none, daily, weekly, monthly, yearly.
For "16th of every month", use recurrence.kind "monthly" and day_of_month 16.
"""
    payload = json.dumps(
        {
            "model": "llama3.1:8b",
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "keep_alive": "5m",
            "options": {"temperature": 0.0, "num_predict": 320},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=35) as response:
        raw = json.loads(response.read().decode("utf-8")).get("response", "{}")
    start = raw.find("{")
    end = raw.rfind("}") + 1
    parsed = json.loads(raw[start:end])
    cleaned = []
    for item in parsed.get("items", []):
        recurrence = Recurrence(**(item.get("recurrence") or {}))
        candidate = ReminderIn(
            title=str(item.get("title") or "New reminder"),
            reminder_type=item.get("reminder_type") or "reminder",
            due_at=item.get("due_at"),
            priority=item.get("priority") or "medium",
            confidence=float(item.get("confidence") or 0.7),
            source_text=item.get("source_text") or text,
            source_type=source_type,
            extractor="ollama",
            source_reason=item.get("source_reason") or "Ollama detected action intent.",
            recurrence=recurrence,
        )
        cleaned.append({"id": str(uuid4()), "needs_confirmation": True, **candidate.model_dump()})
    return cleaned


def token_set(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def overlap_score(left: str, right: str) -> float:
    left_tokens = token_set(left)
    right_tokens = token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def merge_ollama_with_rules(ollama_items: list[dict[str, Any]], rule_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = []
    used_rule_indexes = set()
    for item in ollama_items:
        best_index = None
        best_score = 0.0
        item_key = f"{item.get('title', '')} {item.get('source_text', '')}"
        for index, rule_item in enumerate(rule_items):
            if index in used_rule_indexes:
                continue
            rule_key = f"{rule_item.get('title', '')} {rule_item.get('source_text', '')}"
            score = overlap_score(item_key, rule_key)
            if score > best_score:
                best_score = score
                best_index = index
        if best_index is None or best_score < 0.18:
            merged.append(item)
            continue

        rule_item = rule_items[best_index]
        used_rule_indexes.add(best_index)
        combined = {**item}
        model_title = str(item.get("title") or "").lower()
        rule_title = str(rule_item.get("title") or "")
        if rule_title and (
            "someone" in model_title
            or model_title in {"new reminder", "reminder", "task", "event"}
            or len(rule_title) <= len(str(item.get("title") or "")) + 8
        ):
            combined["title"] = polish_title(rule_title)
        if rule_item.get("due_at"):
            combined["due_at"] = rule_item["due_at"]
        if item.get("reminder_type") == "reminder" and rule_item.get("reminder_type") in {"task", "event", "important_date"}:
            combined["reminder_type"] = rule_item["reminder_type"]
        if rule_item.get("priority") == "high":
            combined["priority"] = "high"
        recurrence = item.get("recurrence") or {}
        if recurrence.get("kind") in {None, "none"} and rule_item.get("recurrence", {}).get("kind") != "none":
            combined["recurrence"] = rule_item["recurrence"]
        combined["extractor"] = "ollama+rules"
        combined["source_reason"] = item.get("source_reason") or rule_item.get("source_reason", "")
        merged.append(combined)

    for index, rule_item in enumerate(rule_items):
        if index not in used_rule_indexes:
            merged.append(rule_item)
    return merged


def reminder_from_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["recurrence"] = {
        "kind": data.pop("recurrence_kind") or "none",
        "interval": data.pop("recurrence_interval") or 1,
        "day_of_month": data.pop("recurrence_day_of_month"),
        "day_of_week": data.pop("recurrence_day_of_week"),
        "month": data.pop("recurrence_month"),
    }
    return data


def recurrence_to_rrule(reminder: dict[str, Any]) -> str | None:
    recurrence = reminder.get("recurrence") or {}
    kind = recurrence.get("kind", "none")
    if kind == "none":
        return None
    interval = recurrence.get("interval") or 1
    if kind == "daily":
        return f"RRULE:FREQ=DAILY;INTERVAL={interval}"
    if kind == "weekly":
        days = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
        day = recurrence.get("day_of_week")
        suffix = f";BYDAY={days[day]}" if day is not None else ""
        return f"RRULE:FREQ=WEEKLY;INTERVAL={interval}{suffix}"
    if kind == "monthly":
        day = recurrence.get("day_of_month")
        suffix = f";BYMONTHDAY={day}" if day else ""
        return f"RRULE:FREQ=MONTHLY;INTERVAL={interval}{suffix}"
    if kind == "yearly":
        day = recurrence.get("day_of_month")
        month = recurrence.get("month")
        suffix = ""
        if month:
            suffix += f";BYMONTH={month}"
        if day:
            suffix += f";BYMONTHDAY={day}"
        return f"RRULE:FREQ=YEARLY;INTERVAL={interval}{suffix}"
    return None


def ics_datetime(value: str | None) -> str:
    if not value:
        return (datetime.now() + timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now() + timedelta(hours=1)
    return parsed.strftime("%Y%m%dT%H%M%S")


def extract_candidates(text: str, now: datetime, source_type: str, use_ollama: bool = True) -> tuple[list[dict[str, Any]], str | None]:
    rule_candidates = rule_extract(text, now, source_type)
    if not use_ollama:
        return rule_candidates, None
    try:
        return merge_ollama_with_rules(
            ollama_extract(text, now, source_type),
            rule_candidates,
        ), None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return rule_candidates, str(exc)


def save_pending_candidates(candidates: list[dict[str, Any]], source_text: str, source_type: str) -> list[dict[str, Any]]:
    created_at = datetime.now().isoformat(timespec="seconds") + IST_OFFSET
    saved = []
    with db() as conn:
        for candidate in candidates:
            pending_id = str(uuid4())
            payload = {**candidate, "pending_id": pending_id}
            conn.execute(
                """
                INSERT INTO pending_candidates (id, candidate_json, source_text, source_type, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (pending_id, json.dumps(payload), source_text, source_type, created_at),
            )
            saved.append(payload)
    return saved


def detect_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        try:
            host = socket.gethostname()
            ip = socket.gethostbyname(host)
            if not ip.startswith("127."):
                return ip
        except OSError:
            return None
    return None


def setup_urls(request: Request) -> dict[str, str]:
    request_host = request.headers.get("host", "127.0.0.1:8001")
    port = request_host.rsplit(":", 1)[1] if ":" in request_host else "8001"
    lan_ip = detect_lan_ip()
    laptop_origin = f"{request.url.scheme}://{request_host}"
    phone_origin = f"http://{lan_ip}:{port}" if lan_ip else laptop_origin
    return {
        "laptop_url": laptop_origin,
        "phone_url": phone_origin,
        "webhook_url": f"{phone_origin}/api/webhook/capture",
        "calendar_url": f"{phone_origin}/api/calendar.ics",
    }


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "manifest.webmanifest")


@app.get("/api/setup")
def setup(request: Request) -> dict[str, Any]:
    urls = setup_urls(request)
    return {
        **urls,
        "shortcut_body": {
            "text": "Shortcut Input",
            "source_type": "iphone_shortcut",
        },
        "calendar_provider": "ics",
        "calendar_sync_status": "ready_for_feed",
    }


@app.post("/api/analyze")
def analyze(payload: AnalyzeRequest) -> dict[str, Any]:
    now = parse_now(payload.now)
    normalized = normalize_text(payload.text)
    candidates, ollama_error = extract_candidates(normalized, now, payload.source_type, payload.use_ollama)
    extractor = candidates[0].get("extractor", "rules") if candidates else "rules"
    return {
        "normalized_text": normalized,
        "extractor": extractor,
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
            (
                id, title, reminder_type, due_at, priority, confidence, source_text,
                source_type, extractor, source_reason, recurrence_kind, recurrence_interval,
                recurrence_day_of_month, recurrence_day_of_week, recurrence_month,
                lead_minutes, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?)
            """,
            (
                reminder_id,
                payload.title,
                payload.reminder_type,
                payload.due_at,
                payload.priority,
                payload.confidence,
                payload.source_text,
                payload.source_type,
                payload.extractor,
                payload.source_reason,
                payload.recurrence.kind,
                payload.recurrence.interval,
                payload.recurrence.day_of_month,
                payload.recurrence.day_of_week,
                payload.recurrence.month,
                payload.lead_minutes,
                created_at,
                created_at,
            ),
        )
    return {"id": reminder_id, **payload.model_dump(), "status": "scheduled", "created_at": created_at}


@app.post("/api/webhook/capture")
def webhook_capture(payload: WebhookCapture) -> dict[str, Any]:
    now = parse_now(payload.now)
    normalized = normalize_text(payload.text)
    candidates, ollama_error = extract_candidates(normalized, now, payload.source_type, True)
    pending = save_pending_candidates(candidates, normalized, payload.source_type)
    return {
        "status": "queued",
        "normalized_text": normalized,
        "count": len(pending),
        "ollama_error": ollama_error,
        "candidates": pending,
    }


@app.get("/api/pending")
def list_pending_candidates() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM pending_candidates ORDER BY created_at DESC").fetchall()
    pending = []
    for row in rows:
        candidate = json.loads(row["candidate_json"])
        candidate["pending_id"] = row["id"]
        candidate["queued_at"] = row["created_at"]
        candidate["source_type"] = candidate.get("source_type") or row["source_type"]
        pending.append(candidate)
    return pending


@app.delete("/api/pending/{pending_id}")
def delete_pending_candidate(pending_id: str) -> dict[str, str]:
    with db() as conn:
        result = conn.execute("DELETE FROM pending_candidates WHERE id = ?", (pending_id,))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Pending candidate not found")
    return {"status": "deleted"}


@app.get("/api/reminders")
def list_reminders() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM reminders ORDER BY COALESCE(due_at, created_at)").fetchall()
    return [reminder_from_row(row) for row in rows]


@app.patch("/api/reminders/{reminder_id}")
def update_reminder(reminder_id: str, payload: ReminderPatch) -> dict[str, Any]:
    if payload.status is None:
        raise HTTPException(status_code=400, detail="No update provided")
    updated_at = datetime.now().isoformat(timespec="seconds") + IST_OFFSET
    with db() as conn:
        result = conn.execute(
            "UPDATE reminders SET status = ?, updated_at = ? WHERE id = ?",
            (payload.status, updated_at, reminder_id),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Reminder not found")
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    return reminder_from_row(row)


@app.delete("/api/reminders/{reminder_id}")
def delete_reminder(reminder_id: str) -> dict[str, str]:
    with db() as conn:
        result = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return {"status": "deleted"}


@app.get("/api/calendar.ics")
def calendar_export() -> Response:
    reminders = list_reminders()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SignalOS//Action Timeline//EN",
        "CALSCALE:GREGORIAN",
    ]
    for reminder in reminders:
        if reminder.get("status") != "scheduled":
            continue
        start = ics_datetime(reminder.get("due_at"))
        end = ics_datetime(reminder.get("due_at"))
        rrule = recurrence_to_rrule(reminder)
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{reminder['id']}@signalos.local",
                f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
                f"DTSTART:{start}",
                f"DTEND:{end}",
                f"SUMMARY:{reminder['title']}",
                f"DESCRIPTION:{reminder.get('source_text') or 'Created by SignalOS'}",
            ]
        )
        if rrule:
            lines.append(rrule)
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines) + "\r\n", media_type="text/calendar")
