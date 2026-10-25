# SignalOS

SignalOS is a local-first GenAI/NLP action inbox that turns messy real-world text into confirmed reminders, tasks, events, notes, and important dates.

It is designed for the kind of information people actually receive every day: WhatsApp-style messages, Hinglish shorthand, college notices, receipts, deadline reminders, travel details, and multi-line notes. Instead of forcing the user to manually create reminders, SignalOS extracts structured action candidates, asks for confirmation, and saves only what the user approves.

## Why SignalOS

Most reminder apps depend on clean manual entry. Most chatbots answer questions but do not turn noisy input into an action system. SignalOS sits in between:

1. Paste or send messy input.
2. Extract actions, dates, priorities, recurrence, and intent.
3. Review/edit before anything is saved.
4. Track confirmed items in one local action timeline.
5. Export scheduled items to a calendar feed.

The core idea is human-in-the-loop automation: AI helps interpret the input, but the user stays in control.

## Current Status

This repository contains a working MVP.

Implemented:

- FastAPI backend
- Vanilla JavaScript frontend with a polished dark UI
- SQLite persistence
- Deterministic NLP extraction for reliable fallback
- Optional local Ollama/Llama extraction and validation
- Multi-action extraction from a single prompt
- Hinglish and casual shorthand support
- Receipt/document-style intent detection
- Editable confirmation cards before saving
- Reminder, task, event, note, and important-date types
- Date, time, recurrence, priority, and confidence extraction
- Timeline filters for active, recurring, completed, and all items
- Deterministic "why this was detected" descriptions on saved reminders
- Postpone, done/reopen, and delete workflows
- Weekly report for completed, pending, overdue, and completion rate
- iPhone Shortcut/webhook capture path
- Phone-accessible local web app setup panel
- `.ics` calendar export
- PWA manifest and service worker shell

Not yet implemented:

- OCR image upload for screenshots
- Direct Google Calendar API sync
- Push notifications
- Hosted deployment
- Automated regression test suite
- Subtasks/progress workflow for larger tasks

## Tech Stack

- Backend: Python, FastAPI, Pydantic, Uvicorn
- Data: SQLite
- Frontend: HTML, CSS, vanilla JavaScript
- Local AI: Ollama with `llama3.1:8b`
- Calendar: ICS feed export
- Mobile bridge: local network web app and iPhone Shortcut webhook

## How The Extraction Works

SignalOS uses a hybrid extraction approach:

1. Deterministic rules handle reliable baseline parsing.
2. Ollama is used automatically when available for local GenAI extraction.
3. The system merges model output with rule-based guardrails.
4. The user reviews every candidate before saving.

The deterministic layer currently handles:

- text normalization
- Hinglish shorthand such as `kal`, `aaj`, `padhai`, `exam dena`, and `jana`
- multi-line and multi-action splitting
- date and time parsing
- monthly/yearly/weekly/daily recurrence detection
- payment, travel, deadline, appointment, and personal-task intent scoring
- priority detection from urgency phrases such as `urgent`, `deadline`, and `MOST IMPORTANT`
- concise title generation from messy input

## Example Inputs

### Single reminder

```text
cll me at 9 pm
```

Expected candidate:

```text
Call me - reminder - today 9:00 PM
```

### Recurring date

```text
my anniversary is on th 16th of every month
```

Expected candidate:

```text
Anniversary - important date - monthly on day 16
```

### Multiple tasks from one prompt

```text
10 am study for exam

2 pm give exam

7pm call gf (MOST IMPORTANT)

also remind me to go to the dentist
```

Expected candidates:

```text
Study for exam - task - 10:00 AM - medium
Give exam - task - 2:00 PM - medium
Call gf - task - 7:00 PM - high
Go to the dentist - task - no specific time - low
```

### Receipt/document-style input

```text
Dear Passenger,

Bus Journey details
From: Delhi To Jaipur (rajasthan) On 21/10/2026
PNR: 2402070,
Seat No: SU6,
Boarding: Kashmiri gate metro gate no.3 At 10:50 PM
```

Expected candidate:

```text
Bus journey: Delhi to Jaipur - event - 21 Oct 2026, 10:50 PM
```

## Running Locally

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Start the app:

```powershell
python -m uvicorn server:app --reload --host 0.0.0.0 --port 8001
```

Open on laptop:

```text
http://127.0.0.1:8001
```

For phone testing on the same Wi-Fi, open the phone URL shown in the in-app "Sync setup" panel.

## Optional Local AI Setup

SignalOS works without Ollama using deterministic extraction.

For local GenAI extraction:

```powershell
ollama pull llama3.1:8b
ollama serve
```

When Ollama is running, SignalOS automatically attempts local model extraction and validates/merges it with deterministic rules. If Ollama is unavailable or times out, the app falls back to rules.

## Free Long-Term Deployment

The recommended no-spend deployment path for personal use is:

- Render Free web service for the FastAPI app
- Neon Free Postgres for persistent online storage
- deterministic extraction online
- Ollama remains a local-only enhancement unless a hosted model provider is added later

This avoids Render's expiring free Postgres database. The app still uses local SQLite when `DATABASE_URL` is not set.

Steps:

1. Create a free Neon project.
2. Copy the pooled Postgres connection string from Neon.
3. Push the latest SignalOS code to GitHub.
4. Open Render and create a new Blueprint from the SignalOS repository.
5. Render will read `render.yaml` and create the web service.
6. In Render, set the `DATABASE_URL` environment variable to the Neon connection string.
7. Deploy, then open the public Render URL.

Important free-tier limits:

- Render Free web services can sleep after inactivity, so the first request may take about a minute
- Neon Free compute can suspend after inactivity, so the first database query may also have a short cold start
- Neon Free storage persists within its free limits, but the app should still be exported/backed up if it becomes important
- hosted deployment does not run your laptop's local Ollama instance

## API Overview

Analyze text:

```text
POST /api/analyze
```

Create confirmed reminder:

```text
POST /api/reminders
```

List reminders:

```text
GET /api/reminders
```

Update reminder status, priority, due date, or progress metadata:

```text
PATCH /api/reminders/{reminder_id}
```

Queue phone/webhook capture for confirmation:

```text
POST /api/webhook/capture
```

Weekly report:

```text
GET /api/report/weekly
```

Calendar feed:

```text
GET /api/calendar.ics
```

Setup URLs for phone, webhook, and calendar:

```text
GET /api/setup
```

## Phone And Calendar Workflow

For the MVP, the laptop runs the SignalOS server and owns the central SQLite database. Laptop browser, phone browser, and iPhone Shortcuts can all talk to the same local server when they are on the same network.

The current notification bridge is calendar-based:

- confirmed reminders are exposed through `/api/calendar.ics`
- the feed can be subscribed to from calendar apps
- direct Google Calendar API sync is planned next

The database already includes calendar sync metadata fields for that next step:

- `calendar_provider`
- `calendar_event_id`
- `calendar_synced_at`
- `calendar_sync_status`

## Project Structure

```text
SignalOS/
|-- server.py                 # FastAPI backend, extraction logic, API routes
|-- requirements.txt          # Python dependencies
|-- data/
|   `-- signalos.db           # Local SQLite database
|-- static/
|   |-- index.html            # Main UI
|   |-- app.js                # Frontend behavior
|   |-- styles.css            # Dark responsive UI
|   |-- manifest.webmanifest  # PWA metadata
|   `-- service-worker.js     # PWA shell
`-- PRESENTATION_NOTES.md     # Short class/demo notes
```

## Roadmap

Near-term:

- Add OCR for screenshot/image uploads
- Add direct Google Calendar API sync
- Add extraction regression tests and benchmark examples
- Add settings for default reminder time and notification method
- Improve phone shortcut documentation

Later:

- Add PWA push notifications
- Add subtasks for multi-step work
- Bring back progress bars after the presentation MVP
- Add hosted deployment option
- Add user accounts or multi-device sync beyond local network mode

## Resume Summary

Built SignalOS, a local-first GenAI/NLP action inbox that converts messy text, Hinglish prompts, receipt-like inputs, and multi-line notes into confirmed tasks, reminders, events, and calendar-ready actions using FastAPI, SQLite, deterministic NLP extraction, Ollama fallback/validation, and a human-in-the-loop confirmation workflow.
