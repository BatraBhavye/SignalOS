from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # Optional unless DATABASE_URL points to Postgres.
    psycopg = None
    dict_row = None


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "signalos.db"
DATABASE_URL = os.getenv("DATABASE_URL", "")
IST_OFFSET = "+05:30"
IST = timezone(timedelta(hours=5, minutes=30))
SESSION_COOKIE = "signalos_session"
SESSION_SECRET = os.getenv("SESSION_SECRET", "signalos-dev-session-secret-change-me")
MAX_USERS = int(os.getenv("MAX_USERS", "0") or "0")
ALLOW_SIGNUPS = os.getenv("ALLOW_SIGNUPS", "true").lower() not in {"0", "false", "no"}

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
    progress_percent: int = Field(default=0, ge=0, le=100)


class ReminderPatch(BaseModel):
    status: Status | None = None
    due_at: str | None = None
    priority: Priority | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100)


class WebhookCapture(BaseModel):
    text: str = Field(..., min_length=1)
    source_type: str = "iphone_shortcut"
    now: str | None = None


class AuthRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=80)
    password: str = Field(..., min_length=8, max_length=200)


def use_postgres() -> bool:
    return DATABASE_URL.startswith(("postgres://", "postgresql://"))


class Database:
    def __init__(self) -> None:
        self.is_postgres = use_postgres()
        if self.is_postgres:
            if psycopg is None:
                raise RuntimeError("psycopg is required when DATABASE_URL uses Postgres.")
            self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        else:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            self.conn = conn

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.conn.close()

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> Any:
        if self.is_postgres:
            sql = sql.replace("?", "%s")
        return self.conn.execute(sql, params)


def db() -> Database:
    if not use_postgres():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return Database()


def database_label() -> str:
    return "postgres" if use_postgres() else "sqlite"


def sqlite_table_columns(conn: Database, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def postgres_table_columns(conn: Database, table: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name AS name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ?
        """,
        (table,),
    ).fetchall()
    return {row["name"] for row in rows}


def table_columns(conn: Database, table: str) -> set[str]:
    if conn.is_postgres:
        return postgres_table_columns(conn, table)
    return sqlite_table_columns(conn, table)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def normalize_username(username: str) -> str:
    return username.strip().lower()


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 150_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, salt, expected = stored_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    return hmac.compare_digest(password_hash(password, salt), stored_hash)


def sign_session(user_id: str) -> str:
    payload = base64.urlsafe_b64encode(user_id.encode("utf-8")).decode("ascii")
    signature = hmac.new(SESSION_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_session(value: str | None) -> str | None:
    if not value or "." not in value:
        return None
    payload, signature = value.rsplit(".", 1)
    expected = hmac.new(SESSION_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        return base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
    except ValueError:
        return None


def public_user(row: Any) -> dict[str, Any]:
    return {"id": row["id"], "username": row["username"], "created_at": row["created_at"]}


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_api_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE api_token = ?", (token,)).fetchone()
    return dict(row) if row else None


def get_current_user(request: Request) -> dict[str, Any]:
    user_id = verify_session(request.cookies.get(SESSION_COOKIE))
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def get_current_user_or_token(request: Request) -> dict[str, Any]:
    token = request.query_params.get("token") or request.headers.get("X-SignalOS-Token")
    user = get_user_by_api_token(token)
    if user:
        return user
    return get_current_user(request)


def set_session_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(user_id),
        httponly=True,
        secure=use_postgres(),
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=use_postgres(), samesite="lax")


def user_count(conn: Database) -> int:
    row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
    return int(row["count"])


def claim_unowned_data(conn: Database, user_id: str) -> None:
    conn.execute("UPDATE reminders SET user_id = ? WHERE user_id IS NULL", (user_id,))
    conn.execute("UPDATE pending_candidates SET user_id = ? WHERE user_id IS NULL", (user_id,))


def ensure_user_columns(conn: Database) -> None:
    existing = table_columns(conn, "users")
    if "api_token" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN api_token TEXT")
    rows = conn.execute("SELECT id FROM users WHERE api_token IS NULL OR api_token = ''").fetchall()
    for row in rows:
        conn.execute("UPDATE users SET api_token = ? WHERE id = ?", (generate_token(), row["id"]))


def ensure_columns(conn: Database) -> None:
    existing = table_columns(conn, "reminders")
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
        "progress_percent": "INTEGER NOT NULL DEFAULT 0",
        "completed_at": "TEXT",
        "postponed_count": "INTEGER NOT NULL DEFAULT 0",
        "last_postponed_at": "TEXT",
        "calendar_provider": "TEXT NOT NULL DEFAULT 'ics'",
        "calendar_event_id": "TEXT",
        "calendar_synced_at": "TEXT",
        "calendar_sync_status": "TEXT NOT NULL DEFAULT 'not_synced'",
        "updated_at": "TEXT",
        "user_id": "TEXT",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE reminders ADD COLUMN {name} {definition}")


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                api_token TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
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
                progress_percent INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                postponed_count INTEGER NOT NULL DEFAULT 0,
                last_postponed_at TEXT,
                calendar_provider TEXT NOT NULL DEFAULT 'ics',
                calendar_event_id TEXT,
                calendar_synced_at TEXT,
                calendar_sync_status TEXT NOT NULL DEFAULT 'not_synced',
                status TEXT NOT NULL DEFAULT 'scheduled',
                user_id TEXT,
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
                user_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        ensure_user_columns(conn)
        ensure_columns(conn)
        existing_pending = table_columns(conn, "pending_candidates")
        if "user_id" not in existing_pending:
            conn.execute("ALTER TABLE pending_candidates ADD COLUMN user_id TEXT")


def parse_now(raw_now: str | None) -> datetime:
    if not raw_now:
        return datetime.now(IST).replace(tzinfo=None)
    try:
        parsed = datetime.fromisoformat(raw_now.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(IST)
        return parsed.replace(tzinfo=None)
    except ValueError:
        return datetime.now(IST).replace(tzinfo=None)


TEXT_REPLACEMENTS = {
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
    r"\baaj\b": "today",
    r"\bkal\b": "tomorrow",
    r"\bparso\b": "day after tomorrow",
    r"\bpadhai\b": "study",
    r"\bpadhna\b": "study",
    r"\bpadna\b": "study",
    r"\bpadho\b": "study",
    r"\bexam dena\b": "give exam",
    r"\bpaper dena\b": "give exam",
    r"\bjana\b": "go",
    r"\bjaana\b": "go",
    r"\bzaroori\b": "important",
    r"\bjaruri\b": "important",
    r"\bbahut important\b": "most important",
    r"\bbohot important\b": "most important",
}


def apply_text_replacements(text: str) -> str:
    cleaned = text.strip()
    for pattern, repl in TEXT_REPLACEMENTS.items():
        cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)
    return cleaned


def normalize_text(text: str) -> str:
    cleaned = apply_text_replacements(text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def split_action_chunks(text: str) -> list[str]:
    cleaned = apply_text_replacements(text)
    cleaned = re.sub(r"\r\n?", "\n", cleaned)
    lines = [
        re.sub(r"\s+", " ", line).strip(" -\t")
        for line in cleaned.split("\n")
        if re.sub(r"\s+", " ", line).strip(" -\t")
    ]
    if len(lines) >= 2:
        return lines
    normalized = normalize_text(cleaned)
    return [
        chunk.strip()
        for chunk in re.split(r"[;]+|(?:\s+\balso\s+)|(?:\s+\band\s+)", normalized, flags=re.IGNORECASE)
        if chunk.strip()
    ]


def matching_score(text: str, patterns: list[tuple[str, int]]) -> int:
    return sum(weight for pattern, weight in patterns if re.search(pattern, text, re.IGNORECASE))


def duration_value(raw_value: str) -> int | None:
    words = {
        "a": 1,
        "an": 1,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    if raw_value.isdigit():
        return int(raw_value)
    return words.get(raw_value.lower())


def clean_title_fragment(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value)
    cleaned = re.sub(r"\((?:most\s+)?(?:important|urgent|asap|critical)[^)]*\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:karna|karni|krna)\s+hai\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:in|after)\s+(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
        r"(?:minutes?|mins?|hours?|hrs?)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(?:in|after)\s+half\s+(?:an?\s+)?hour\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:by|before|on|at)\s+(?:today|tomorrow|tonight|kal|aaj)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:on|at|by|before)\s+\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:on|at|by|before)\s+\d{1,2}(?:st|nd|rd|th)?\s+[a-z]{3,9}\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:at|by|before)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:pnr|seat(?:\s+no)?|address|boarding|invoice|order|booking)\s*[:#-].*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" .,!?:;-")
    return cleaned


def title_case_place(value: str) -> str:
    cleaned = clean_title_fragment(value)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    cleaned = cleaned.strip(" .,!?:;-")
    return " ".join(part.capitalize() for part in cleaned.split())


def extract_route_title(text: str) -> str | None:
    route_match = re.search(
        r"\bfrom\s*:?\s*([a-z][a-z\s.'()-]{1,45}?)\s+to\s*:?\s*([a-z][a-z\s.'()-]{1,45}?)(?:\s+on\b|,|\n|$)",
        text,
        re.IGNORECASE,
    )
    if not route_match:
        return None
    origin = title_case_place(route_match.group(1))
    destination = title_case_place(route_match.group(2))
    if not origin or not destination:
        return None
    vehicle = "Travel"
    if re.search(r"\bbus\b", text, re.IGNORECASE):
        vehicle = "Bus"
    elif re.search(r"\b(train|railway)\b", text, re.IGNORECASE):
        vehicle = "Train"
    elif re.search(r"\b(flight|airline|airport)\b", text, re.IGNORECASE):
        vehicle = "Flight"
    return f"{vehicle} journey: {origin} to {destination}"


def extract_payment_title(text: str) -> str | None:
    lower = text.lower()
    bill_terms = [
        ("tuition fees", r"\b(tuition|school|college|semester)\b.*\bfees?\b|\bfees?\b.*\b(tuition|school|college|semester)\b"),
        ("fees", r"\bfees?\b"),
        ("electricity bill", r"\belectricity\b|\bpower bill\b"),
        ("phone bill", r"\b(phone|mobile|postpaid)\b.*\bbill\b"),
        ("rent", r"\brent\b"),
        ("invoice", r"\binvoice\b"),
        ("bill", r"\bbill\b"),
    ]
    for label, pattern in bill_terms:
        if re.search(pattern, lower):
            return f"Pay {label}"
    if re.search(r"\b(pay|payment|amount due|due amount)\b", lower):
        return "Make payment"
    return None


def first_meaningful_fragment(text: str) -> str:
    fragments = [
        clean_title_fragment(fragment)
        for fragment in re.split(r"[\n.;,]+", text)
        if clean_title_fragment(fragment)
    ]
    ignored = re.compile(r"^(dear|hello|hi|thanks|thank you|regards|address|seat|pnr)\b", re.IGNORECASE)
    for fragment in fragments:
        if not ignored.search(fragment) and len(fragment.split()) >= 2:
            return fragment
    return fragments[0] if fragments else ""


def classify_intent(text: str) -> dict[str, Any]:
    lower = text.lower()
    profiles = [
        {
            "key": "travel",
            "reminder_type": "event",
            "patterns": [
                (r"\bpnr\b", 3),
                (r"\bseat\s*(?:no|number)?\b", 2),
                (r"\bboarding\b", 2),
                (r"\bfrom\s*:?.{1,60}\bto\s*:?", 3),
                (r"\b(bus|train|flight|journey|ticket|passenger|booking)\b", 2),
            ],
            "reason": "Detected a scheduled booking from route, boarding, seat, or ticket details.",
        },
        {
            "key": "payment",
            "reminder_type": "task",
            "patterns": [
                (r"\b(pay|payment|amount due|due amount)\b", 3),
                (r"\b(fee|fees|bill|invoice|rent|emi)\b", 2),
                (r"\b(last date|due date|pay before|before end of day)\b", 2),
            ],
            "reason": "Detected a payment task from bill, fee, invoice, or due-date language.",
        },
        {
            "key": "deadline",
            "reminder_type": "task",
            "patterns": [
                (r"\b(deadline|last date|due|submit|submission)\b", 3),
                (r"\b(assignment|form|report|project|application|exam|paper|study)\b", 2),
            ],
            "reason": "Detected a task deadline from due-date or submission language.",
        },
        {
            "key": "personal_task",
            "reminder_type": "task",
            "patterns": [
                (r"\b(study|revise|prepare|give exam|exam|paper)\b", 3),
                (r"\b(call|message|text|go|visit|dentist|doctor)\b", 3),
            ],
            "reason": "Detected a personal action item from task-style language.",
        },
        {
            "key": "appointment",
            "reminder_type": "event",
            "patterns": [
                (r"\b(meeting|appointment|interview|session|webinar|class|event)\b", 3),
                (r"\b(venue|location|join|attend|scheduled)\b", 1),
            ],
            "reason": "Detected a scheduled event from appointment or meeting language.",
        },
    ]
    best = {"key": "", "reminder_type": infer_type_basic(text), "score": 0, "reason": ""}
    for profile in profiles:
        score = matching_score(lower, profile["patterns"])
        if score > best["score"]:
            best = {
                "key": profile["key"],
                "reminder_type": profile["reminder_type"],
                "score": score,
                "reason": profile["reason"],
            }
    if best["score"] < 3:
        best["key"] = ""
        best["reminder_type"] = infer_type_basic(text)
        best["reason"] = ""
    return best


def infer_type_basic(text: str) -> ReminderType:
    lower = text.lower()
    if re.search(r"\b(anniversary|birthday|bday|birth day)\b", lower):
        return "important_date"
    if re.search(r"\b(meeting|class|appointment|interview|session|event|webinar)\b", lower):
        return "event"
    if re.search(r"\b(submit|finish|complete|send|pay|fill|upload|apply|book|study|revise|prepare|exam|paper)\b", lower):
        return "task"
    if re.search(r"\b(call|message|text|remind|ping|wake|gym|chalna|jana|jaana|go|visit|dentist|doctor)\b", lower):
        return "reminder"
    return "note"


def infer_type(text: str) -> ReminderType:
    return classify_intent(text)["reminder_type"]


def priority(text: str, due_at: datetime | None, now: datetime) -> Priority:
    lower = text.lower()
    if re.search(r"\b(urgent|asap|immediately|last date|deadline|important|most important|critical)\b", lower):
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
    half_hour_match = re.search(r"\b(?:in|after)\s+half\s+(?:an?\s+)?hour\b", lower)
    if half_hour_match:
        return (now + timedelta(minutes=30)).replace(second=0, microsecond=0)

    relative_match = re.search(
        r"\b(?:in|after)\s+(a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
        r"(minutes?|mins?|hours?|hrs?)\b",
        lower,
    )
    if relative_match:
        amount = duration_value(relative_match.group(1))
        unit = relative_match.group(2)
        if amount is not None:
            delta = timedelta(hours=amount) if unit.startswith(("hour", "hr")) else timedelta(minutes=amount)
            return (now + delta).replace(second=0, microsecond=0)

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
    intent = classify_intent(text)
    if intent["key"] == "travel":
        route_title = extract_route_title(text)
        if route_title:
            return route_title
        if re.search(r"\bbus\b", lower):
            return "Bus journey"
        if re.search(r"\btrain|railway\b", lower):
            return "Train journey"
        if re.search(r"\bflight|airline|airport\b", lower):
            return "Flight booking"
        return "Travel booking"
    if intent["key"] == "payment":
        payment_title = extract_payment_title(text)
        if payment_title:
            return payment_title
    if intent["key"] == "deadline":
        subject_match = re.search(r"\b(?:submit|submission|deadline|due)\s+(?:for\s+)?(.+)", text, flags=re.IGNORECASE)
        if subject_match:
            subject = clean_title_fragment(subject_match.group(1))
            if subject:
                return polish_title(f"Submit {subject}" if "submit" not in subject.lower() else subject)
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
        r"\b(call|message|text|submit|finish|complete|send|pay|fill|upload|apply|book|study|revise|prepare|give|go|visit)\s+(.+)",
    ]
    title = text
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
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
    title = clean_title_fragment(title)
    title = re.sub(r"\s+", " ", title).strip(" .,!?:;")
    if len(title.split()) > 9 or len(title) > 70:
        title = first_meaningful_fragment(text)
    if len(title) > 70:
        title = title[:67].rstrip(" .,!?:;-") + "..."
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
    document_intent = classify_intent(normalized)
    if document_intent["score"] >= 5 and document_intent["key"] == "travel":
        chunks = [normalized]
    else:
        chunks = split_action_chunks(text)
    candidates = []
    for chunk in chunks:
        intent = classify_intent(chunk)
        reminder_type = intent["reminder_type"]
        recurrence = parse_recurrence(chunk, now)
        date_base = parse_date(chunk, now)
        due = parse_time(chunk, date_base, now) or next_recurrence_due(recurrence, now)
        has_action = reminder_type != "note" or due is not None or re.search(
            r"\b(deadline|last date|due|remind|task|important|pnr|boarding|invoice|booking)\b",
            chunk,
            re.IGNORECASE,
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
        if intent["score"] >= 5:
            confidence += 0.08
        if len(chunk.split()) <= 12:
            confidence += 0.04
        reason = recurrence_reason(recurrence) or intent["reason"] or "Detected action and time signals with the rules engine."
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
Support Hinglish and casual shorthand, for example "kal", "aaj", "padhna", "exam dena", and "jana".
If one message contains multiple lines or multiple action clauses, return one item per action.
Treat "important", "most important", "urgent", "zaroori", and "jaruri" as high priority signals.
When text looks like a document, receipt, ticket, bill, booking, or screenshot OCR, infer the user's motive.
Use concise titles such as "Bus journey: Delhi to Jaipur", "Pay fees", or "Submit assignment"; never use the full source text as the title.
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
        if rule_item.get("reminder_type") in {"task", "event", "important_date", "reminder", "note"}:
            combined["reminder_type"] = rule_item["reminder_type"]
        if rule_item.get("priority"):
            combined["priority"] = rule_item["priority"]
        recurrence = item.get("recurrence") or {}
        if recurrence.get("kind") in {None, "none"} and rule_item.get("recurrence", {}).get("kind") != "none":
            combined["recurrence"] = rule_item["recurrence"]
        combined["extractor"] = "ollama+rules"
        if item.get("source_reason") and item.get("source_reason") != "Ollama detected action intent.":
            combined["source_reason"] = item["source_reason"]
        else:
            combined["source_reason"] = rule_item.get("source_reason", "")
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


def parse_stored_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


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


def save_pending_candidates(candidates: list[dict[str, Any]], source_text: str, source_type: str, user_id: str) -> list[dict[str, Any]]:
    created_at = datetime.now().isoformat(timespec="seconds") + IST_OFFSET
    saved = []
    with db() as conn:
        for candidate in candidates:
            pending_id = str(uuid4())
            payload = {**candidate, "pending_id": pending_id}
            conn.execute(
                """
                INSERT INTO pending_candidates (id, candidate_json, source_text, source_type, user_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (pending_id, json.dumps(payload), source_text, source_type, user_id, created_at),
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
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    public_origin = os.getenv("RENDER_EXTERNAL_URL")
    lan_ip = detect_lan_ip()
    laptop_origin = public_origin or f"{scheme}://{request_host}"
    is_local_host = request_host.startswith(("127.0.0.1", "localhost", "0.0.0.0"))
    phone_origin = f"http://{lan_ip}:{port}" if is_local_host and lan_ip else laptop_origin
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


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    user_id = verify_session(request.cookies.get(SESSION_COOKIE))
    user = get_user_by_id(user_id) if user_id else None
    with db() as conn:
        count = user_count(conn)
    return {
        "authenticated": bool(user),
        "user": public_user(user) if user else None,
        "allow_signups": ALLOW_SIGNUPS and (MAX_USERS == 0 or count < MAX_USERS),
        "max_users": MAX_USERS or None,
        "user_count": count,
    }


@app.post("/api/auth/signup")
def auth_signup(payload: AuthRequest, response: Response) -> dict[str, Any]:
    if not ALLOW_SIGNUPS:
        raise HTTPException(status_code=403, detail="Signups are disabled")
    username = normalize_username(payload.username)
    if not re.fullmatch(r"[a-z0-9_.@-]+", username):
        raise HTTPException(status_code=400, detail="Use letters, numbers, dot, underscore, hyphen, or @")
    user_id = str(uuid4())
    created_at = datetime.now().isoformat(timespec="seconds") + IST_OFFSET
    with db() as conn:
        count = user_count(conn)
        if MAX_USERS and count >= MAX_USERS:
            raise HTTPException(status_code=403, detail="User limit reached")
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already exists")
        conn.execute(
            "INSERT INTO users (id, username, password_hash, api_token, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, password_hash(payload.password), generate_token(), created_at),
        )
        if count == 0:
            claim_unowned_data(conn, user_id)
    set_session_cookie(response, user_id)
    return {"user": {"id": user_id, "username": username, "created_at": created_at}}


@app.post("/api/auth/login")
def auth_login(payload: AuthRequest, response: Response) -> dict[str, Any]:
    username = normalize_username(payload.username)
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    set_session_cookie(response, row["id"])
    return {"user": public_user(row)}


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict[str, str]:
    clear_session_cookie(response)
    return {"status": "logged_out"}


@app.get("/api/setup")
def setup(request: Request) -> dict[str, Any]:
    urls = setup_urls(request)
    user_id = verify_session(request.cookies.get(SESSION_COOKIE))
    user = get_user_by_id(user_id) if user_id else None
    if user and user.get("api_token"):
        urls["webhook_url"] = f"{urls['phone_url']}/api/webhook/capture?token={user['api_token']}"
        urls["calendar_url"] = f"{urls['phone_url']}/api/calendar.ics?token={user['api_token']}"
    return {
        **urls,
        "shortcut_body": {
            "text": "Shortcut Input",
            "source_type": "iphone_shortcut",
        },
        "calendar_provider": "ics",
        "calendar_sync_status": "ready_for_feed",
        "database": database_label(),
    }


@app.post("/api/analyze")
def analyze(payload: AnalyzeRequest, request: Request) -> dict[str, Any]:
    get_current_user(request)
    now = parse_now(payload.now)
    normalized = normalize_text(payload.text)
    candidates, ollama_error = extract_candidates(payload.text, now, payload.source_type, payload.use_ollama)
    extractor = candidates[0].get("extractor", "rules") if candidates else "rules"
    return {
        "normalized_text": normalized,
        "extractor": extractor,
        "ollama_error": ollama_error,
        "candidates": candidates,
    }


@app.post("/api/reminders")
def create_reminder(payload: ReminderIn, request: Request) -> dict[str, Any]:
    user = get_current_user(request)
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
                lead_minutes, progress_percent, status, user_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?)
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
                payload.progress_percent,
                user["id"],
                created_at,
                created_at,
            ),
        )
    return {"id": reminder_id, **payload.model_dump(), "status": "scheduled", "created_at": created_at}


@app.post("/api/webhook/capture")
def webhook_capture(payload: WebhookCapture, request: Request) -> dict[str, Any]:
    user = get_current_user_or_token(request)
    now = parse_now(payload.now)
    normalized = normalize_text(payload.text)
    candidates, ollama_error = extract_candidates(payload.text, now, payload.source_type, True)
    pending = save_pending_candidates(candidates, normalized, payload.source_type, user["id"])
    return {
        "status": "queued",
        "normalized_text": normalized,
        "count": len(pending),
        "ollama_error": ollama_error,
        "candidates": pending,
    }


@app.get("/api/pending")
def list_pending_candidates(request: Request) -> list[dict[str, Any]]:
    user = get_current_user(request)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_candidates WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],),
        ).fetchall()
    pending = []
    for row in rows:
        candidate = json.loads(row["candidate_json"])
        candidate["pending_id"] = row["id"]
        candidate["queued_at"] = row["created_at"]
        candidate["source_type"] = candidate.get("source_type") or row["source_type"]
        pending.append(candidate)
    return pending


@app.delete("/api/pending/{pending_id}")
def delete_pending_candidate(pending_id: str, request: Request) -> dict[str, str]:
    user = get_current_user(request)
    with db() as conn:
        result = conn.execute("DELETE FROM pending_candidates WHERE id = ? AND user_id = ?", (pending_id, user["id"]))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Pending candidate not found")
    return {"status": "deleted"}


@app.get("/api/reminders")
def list_reminders(request: Request) -> list[dict[str, Any]]:
    user = get_current_user(request)
    return list_reminders_for_user(user["id"])


def list_reminders_for_user(user_id: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE user_id = ? ORDER BY COALESCE(due_at, created_at)",
            (user_id,),
        ).fetchall()
    return [reminder_from_row(row) for row in rows]


@app.patch("/api/reminders/{reminder_id}")
def update_reminder(reminder_id: str, payload: ReminderPatch, request: Request) -> dict[str, Any]:
    user = get_current_user(request)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No update provided")
    updated_at = datetime.now().isoformat(timespec="seconds") + IST_OFFSET
    with db() as conn:
        current = conn.execute("SELECT * FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user["id"])).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Reminder not found")
        set_parts = ["updated_at = ?"]
        values: list[Any] = [updated_at]

        if "status" in updates and payload.status is not None:
            set_parts.append("status = ?")
            values.append(payload.status)
            if payload.status == "completed":
                set_parts.append("completed_at = ?")
                values.append(updated_at)
                set_parts.append("progress_percent = ?")
                values.append(100)
            elif payload.status == "scheduled":
                set_parts.append("completed_at = ?")
                values.append(None)

        if "due_at" in updates:
            set_parts.append("due_at = ?")
            values.append(payload.due_at)
            if payload.due_at and payload.due_at != current["due_at"]:
                set_parts.append("postponed_count = postponed_count + 1")
                set_parts.append("last_postponed_at = ?")
                values.append(updated_at)

        if "priority" in updates and payload.priority is not None:
            set_parts.append("priority = ?")
            values.append(payload.priority)

        if "progress_percent" in updates and payload.progress_percent is not None:
            set_parts.append("progress_percent = ?")
            values.append(payload.progress_percent)

        values.extend([reminder_id, user["id"]])
        conn.execute(
            f"UPDATE reminders SET {', '.join(set_parts)} WHERE id = ? AND user_id = ?",
            values,
        )
        row = conn.execute("SELECT * FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user["id"])).fetchone()
    return reminder_from_row(row)


@app.delete("/api/reminders/{reminder_id}")
def delete_reminder(reminder_id: str, request: Request) -> dict[str, str]:
    user = get_current_user(request)
    with db() as conn:
        result = conn.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user["id"]))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return {"status": "deleted"}


@app.get("/api/report/weekly")
def weekly_report(request: Request) -> dict[str, Any]:
    user = get_current_user(request)
    reminders = list_reminders_for_user(user["id"])
    now = datetime.now()
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    scheduled = [item for item in reminders if item.get("status") == "scheduled"]
    completed = [item for item in reminders if item.get("status") == "completed"]
    completed_this_week = [
        item for item in completed
        if (completed_at := parse_stored_datetime(item.get("completed_at"))) and completed_at >= start
    ]
    overdue = [
        item for item in scheduled
        if (due_at := parse_stored_datetime(item.get("due_at"))) and due_at < now
    ]
    active_tasks = [item for item in scheduled if item.get("reminder_type") == "task"]
    progress_values = [int(item.get("progress_percent") or 0) for item in active_tasks]
    average_progress = round(sum(progress_values) / len(progress_values)) if progress_values else 0
    denominator = len(completed_this_week) + len(scheduled)
    completion_rate = round((len(completed_this_week) / denominator) * 100) if denominator else 0
    return {
        "week_start": start.isoformat(timespec="seconds") + IST_OFFSET,
        "completed_this_week": len(completed_this_week),
        "pending": len(scheduled),
        "overdue": len(overdue),
        "completion_rate": completion_rate,
        "average_progress": average_progress,
    }


@app.get("/api/calendar.ics")
def calendar_export(request: Request) -> Response:
    user = get_current_user_or_token(request)
    reminders = list_reminders_for_user(user["id"])
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
