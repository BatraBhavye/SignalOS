from __future__ import annotations

import os
from datetime import datetime, timedelta

# Enforce hermetic test environment prior to importing server
os.environ["DATABASE_URL"] = ""
os.environ["SESSION_SECRET"] = "hermetic-test-session-secret-key-32-chars-min"
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"  # Dummy unreachable endpoint
os.environ["AI_BRIDGE_URL"] = ""
os.environ["AI_BRIDGE_TOKEN"] = ""
os.environ["MAX_USERS"] = "0"
os.environ["ALLOW_SIGNUPS"] = "true"

import pytest
from fastapi.testclient import TestClient

import server
from server import Recurrence


@pytest.fixture(autouse=True)
def isolate_database(tmp_path, monkeypatch):
    """Hermetic SQLite database using tmp_path for complete isolation."""
    test_db = tmp_path / "test_signalos.db"
    monkeypatch.setattr(server, "DB_PATH", test_db)
    monkeypatch.setattr(server, "DATABASE_URL", "")
    server.init_db()
    yield


@pytest.fixture
def client():
    """FastAPI TestClient instance using the autouse isolated database."""
    with TestClient(server.app) as test_client:
        yield test_client


def test_health_endpoint_unauthenticated(client: TestClient):
    """GET /health must return 200 and {'status': 'ok'} without authentication."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_infer_priority_deterministic():
    """Verify priority inference based on urgency keywords and relative deadlines."""
    ref_time = datetime(2026, 11, 15, 10, 0)

    # High priority due to urgent keywords
    assert server.priority("URGENT: submit tax documents", due_at=None, now=ref_time) == "high"
    assert server.priority("assignment deadline tomorrow", due_at=None, now=ref_time) == "high"
    assert server.priority("critical bug fix needed immediately", due_at=None, now=ref_time) == "high"

    # Medium priority due to imminent due date (<= 2 days)
    imminent_due = ref_time + timedelta(days=1)
    assert server.priority("Call plumber", due_at=imminent_due, now=ref_time) == "medium"

    # Low priority when no keywords and due far in advance
    distant_due = ref_time + timedelta(days=7)
    assert server.priority("Buy lightbulbs", due_at=distant_due, now=ref_time) == "low"


def test_relative_date_parsing():
    """Verify relative date parsing for tomorrow, Hinglish 'kal', and day after tomorrow."""
    ref_time = datetime(2026, 11, 15, 10, 0)  # Sunday

    # 'tomorrow' maps to now + 1 day
    parsed_tomorrow = server.parse_date("finish project tomorrow", now=ref_time)
    assert parsed_tomorrow.date() == (ref_time + timedelta(days=1)).date()

    # Hinglish 'kal' maps to tomorrow (now + 1 day)
    parsed_kal = server.parse_date("kal exam dena hai", now=ref_time)
    assert parsed_kal.date() == (ref_time + timedelta(days=1)).date()

    # 'day after tomorrow' / Hinglish 'parso' (normalized) maps to now + 2 days
    parsed_after = server.parse_date("day after tomorrow submit assignment", now=ref_time)
    assert parsed_after.date() == (ref_time + timedelta(days=2)).date()


def test_relative_time_parsing():
    """Verify time extraction for specific clock times and day periods."""
    ref_time = datetime(2026, 11, 15, 10, 0)
    base_date = datetime(2026, 11, 16, 0, 0)  # Reference target day

    # Specific 12-hour clock times
    due_5pm = server.parse_time("remind me tomorrow 5pm", date_base=base_date, now=ref_time)
    assert due_5pm == datetime(2026, 11, 16, 17, 0)

    due_930am = server.parse_time("standup at 9:30am", date_base=base_date, now=ref_time)
    assert due_930am == datetime(2026, 11, 16, 9, 30)

    # Named time-of-day periods
    due_morning = server.parse_time("read paper in the morning", date_base=base_date, now=ref_time)
    assert due_morning == datetime(2026, 11, 16, 9, 0)

    due_tonight = server.parse_time("review pull request tonight", date_base=base_date, now=ref_time)
    assert due_tonight == datetime(2026, 11, 16, 21, 0)


def test_relative_duration_parsing():
    """Verify offset durations ('in 2 hours', 'after half hour')."""
    ref_time = datetime(2026, 11, 15, 10, 0)
    base_date = ref_time

    due_2hrs = server.parse_time("call client in 2 hours", date_base=base_date, now=ref_time)
    assert due_2hrs == datetime(2026, 11, 15, 12, 0)

    due_half_hr = server.parse_time("check pizza after half hour", date_base=base_date, now=ref_time)
    assert due_half_hr == datetime(2026, 11, 15, 10, 30)


def test_action_categorization_and_intent():
    """Verify intent classification and reminder_type inference."""
    travel_text = "Flight 6E 204 from Delhi to Mumbai seat 12B boarding 6am"
    assert server.infer_type(travel_text) == "event"
    assert server.classify_intent(travel_text)["key"] == "travel"

    payment_text = "Pay electricity bill before 5pm"
    assert server.infer_type(payment_text) == "task"
    assert server.classify_intent(payment_text)["key"] == "payment"

    deadline_text = "Submit AI project report deadline tomorrow"
    assert server.infer_type(deadline_text) == "task"
    assert server.classify_intent(deadline_text)["key"] == "deadline"

    appointment_text = "Team sync meeting tomorrow"
    assert server.infer_type(appointment_text) == "event"
    assert server.classify_intent(appointment_text)["key"] == "appointment"

    personal_text = "Dentist visit next Monday"
    assert server.infer_type(personal_text) == "task"
    assert server.classify_intent(personal_text)["key"] == "personal_task"


def test_parse_recurrence_patterns():
    """Verify recurrence parsing for daily, weekly, and monthly cadences."""
    ref_time = datetime(2026, 11, 15, 10, 0)

    daily = server.parse_recurrence("take vitamins daily", now=ref_time)
    assert daily == Recurrence(kind="daily")

    weekly = server.parse_recurrence("team retrospective every monday", now=ref_time)
    assert weekly == Recurrence(kind="weekly", day_of_week=0)

    monthly = server.parse_recurrence("pay rent every month on the 5th", now=ref_time)
    assert monthly == Recurrence(kind="monthly", day_of_month=5)


def test_text_normalization_and_shorthand():
    """Verify slang, Hinglish, and shorthand normalization."""
    raw_shorthand = "pls cll me tmrw eod for assgn sub"
    normalized = server.normalize_text(raw_shorthand)
    assert normalized == "please call me tomorrow end of day for assignment submit"

    hinglish_text = "kal padhai karni hai and jana hai"
    normalized_hinglish = server.normalize_text(hinglish_text)
    assert "tomorrow" in normalized_hinglish
    assert "study" in normalized_hinglish
    assert "go" in normalized_hinglish


def test_title_extraction_from_messy_input():
    """Verify concise, polished title generation from noisy text."""
    title_reminder = server.title_from_text("remind me to buy groceries tomorrow at 5pm", "reminder")
    assert title_reminder == "Buy groceries"

    title_task = server.title_from_text("Submit machine learning report by tomorrow", "task")
    assert title_task == "Submit machine learning report"

    title_birthday = server.title_from_text("Mom's birthday next month", "important_date")
    assert title_birthday == "Birthday"


def test_rule_extract_end_to_end_deterministic():
    """Verify full deterministic rule extraction pipeline on noisy text."""
    ref_time = datetime(2026, 11, 15, 10, 0)
    input_text = "Urgent: submit assignment tomorrow at 5pm"

    candidates = server.rule_extract(input_text, now=ref_time, source_type="text")
    assert len(candidates) == 1

    candidate = candidates[0]
    assert candidate["reminder_type"] == "task"
    assert candidate["priority"] == "high"
    assert candidate["due_at"] == "2026-11-16T17:00+05:30"
    assert candidate["extractor"] == "rules"
    assert candidate["needs_confirmation"] is True
    assert "assignment" in candidate["title"].lower()