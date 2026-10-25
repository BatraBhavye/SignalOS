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
- Neon/Postgres persistence for hosted deployment
- Username/password login with per-user data isolation
- Configurable signup cap with `MAX_USERS`
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
- Per-user tokenized `.ics` calendar feed
- Optional per-user Google Calendar OAuth sync
- Authenticated local AI bridge for hosted deployments
- Smart AI fallback that skips Ollama on clear deterministic parses
- Render + Neon free long-term deployment path
- PWA manifest and service worker shell

Not yet implemented:

- OCR image upload for screenshots
- Push notifications
- Automated regression test suite
- Subtasks/progress workflow for larger tasks

## Tech Stack

- Backend: Python, FastAPI, Pydantic, Uvicorn
- Data: SQLite
- Frontend: HTML, CSS, vanilla JavaScript
- Local AI: Ollama with `llama3.1:8b`
- Calendar: per-user ICS feed, optional Google Calendar OAuth sync
- Mobile bridge: local network web app and iPhone Shortcut webhook

## How The Extraction Works

SignalOS uses a hybrid extraction approach:

1. Deterministic rules handle reliable baseline parsing.
2. A smart fallback decides whether AI review is needed.
3. Ollama runs only for uncertain, ambiguous, Hinglish, multi-action, or document-like input.
4. The system merges model output with rule-based guardrails.
5. The user reviews every candidate before saving.

The deterministic layer currently handles:

- text normalization
- Hinglish shorthand such as `kal`, `aaj`, `padhai`, `exam dena`, and `jana`
- multi-line and multi-action splitting
- date and time parsing
- monthly/yearly/weekly/daily recurrence detection
- payment, travel, deadline, appointment, and personal-task intent scoring
- priority detection from urgency phrases such as `urgent`, `deadline`, and `MOST IMPORTANT`
- concise title generation from messy input

## Auth And Data Isolation

SignalOS uses simple first-party authentication:

- username/password signup and login
- salted PBKDF2 password hashes
- signed HTTP-only session cookies
- each reminder and pending candidate belongs to one user
- optional `MAX_USERS` cap for personal deployments
- tokenized calendar and webhook URLs are generated per user through `/api/setup`
- Google Calendar tokens are stored per user when optional Google sync is configured

For a personal deployment, set `MAX_USERS=2` on Render to limit the instance to two accounts. The open-source default allows signups unless the deployer disables or limits them.

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

For local phone testing on the same Wi-Fi, open `http://YOUR-LAPTOP-LAN-IP:8001` on the phone while the local server is running.

For hosted use, open the Render URL from phone or laptop. The app stores data in the configured central database, so both devices see the same reminders after signing into the same SignalOS account.

## Optional Local AI Setup

SignalOS works without Ollama using deterministic extraction.

For local GenAI extraction while running the app on the same laptop:

```powershell
ollama pull llama3.1:8b
ollama serve
```

When Ollama is running, SignalOS uses it only when deterministic extraction looks uncertain or complex. Clear prompts stay fast with rules only. If Ollama is unavailable or times out, the app falls back to rules.

## Optional AI Bridge For Hosted App

The hosted Render app cannot directly access Ollama running on your laptop unless the laptop exposes a small authenticated bridge.

The bridge behavior is:

- laptop on + bridge tunnel online: hosted SignalOS can use local Ollama for AI review
- laptop off or tunnel offline: hosted SignalOS stays online and falls back to deterministic rules
- no AI token cost: Ollama runs locally on your laptop
- bridge is protected with `AI_BRIDGE_TOKEN`

On your laptop, start Ollama:

```powershell
ollama serve
```

Create a private bridge config:

```powershell
copy .env.bridge.example .env.bridge.local
```

Edit `.env.bridge.local` and replace `AI_BRIDGE_TOKEN` with a private random token of at least 32 characters. This file is ignored by Git.

Then double-click:

```text
start_ai_bridge.bat
```

`start_ai_bridge.bat` is intentionally ignored by Git so your personal startup flow stays private. GitHub includes `start_ai_bridge.example.bat` as the template for other users.

Or run the same bridge manually:

```powershell
python -m uvicorn ai_bridge:app --host 127.0.0.1 --port 8787
```

Expose port `8787` with a secure tunnel such as Cloudflare Tunnel, Tailscale Funnel, or ngrok. Then set these environment variables on Render:

```text
AI_BRIDGE_URL=https://your-tunnel-url
AI_BRIDGE_TOKEN=the-same-long-random-secret
OLLAMA_MODEL=llama3.1:8b
```

The SignalOS UI shows `AI bridge online` when the bridge is reachable. If it shows `AI offline, rules active`, reminders still work with deterministic extraction.

Bridge safety checklist:

- use HTTPS tunnel URLs only
- keep `ai_bridge.py` bound to `127.0.0.1`
- never expose Ollama port `11434` directly
- use a random `AI_BRIDGE_TOKEN` with at least 32 characters
- keep the same token only in your laptop environment and Render environment variables
- rotate the token if it is ever pasted somewhere public
- stop the bridge when you do not want the hosted app to use laptop AI
- leave deterministic rules enabled so the hosted app works when the bridge is offline

Optional bridge limit:

```text
AI_BRIDGE_RATE_LIMIT=30
```

This limits bridge calls per client per minute before the request reaches Ollama.

## Free Long-Term Deployment

The recommended no-spend deployment path for personal use is:

- Render Free web service for the FastAPI app
- Neon Free Postgres for persistent online storage
- deterministic extraction online
- optional AI bridge to use your laptop's local Ollama when it is awake

This avoids Render's expiring free Postgres database. The app still uses local SQLite when `DATABASE_URL` is not set.

Steps:

1. Create a free Neon project.
2. Copy the pooled Postgres connection string from Neon.
3. Push the latest SignalOS code to GitHub.
4. Open Render and create a new Blueprint from the SignalOS repository.
5. Render will read `render.yaml` and create the web service.
6. In Render, set the `DATABASE_URL` environment variable to the Neon connection string.
7. Set `SESSION_SECRET` to a long random value.
8. Optionally set `MAX_USERS=2` for a personal two-user deployment.
9. Deploy, then open the public Render URL.

Important free-tier limits:

- Render Free web services can sleep after inactivity, so the first request may take about a minute
- Neon Free compute can suspend after inactivity, so the first database query may also have a short cold start
- Neon Free storage persists within its free limits, but the app should still be exported/backed up if it becomes important
- hosted deployment only uses your laptop's local Ollama when `AI_BRIDGE_URL` is reachable
- the open-source default allows signups; set `MAX_USERS` to cap accounts on your own deployment

Useful environment variables:

```text
DATABASE_URL=postgresql://...
SESSION_SECRET=long-random-secret
MAX_USERS=2
ALLOW_SIGNUPS=true
AI_BRIDGE_URL=https://your-tunnel-url
AI_BRIDGE_TOKEN=long-random-bridge-secret
OLLAMA_MODEL=llama3.1:8b
GOOGLE_CLIENT_ID=google-oauth-client-id
GOOGLE_CLIENT_SECRET=google-oauth-client-secret
GOOGLE_REDIRECT_URI=https://your-render-url/api/calendar/google/callback
```

## Google Calendar OAuth Setup

To enable user-oriented Google Calendar sync:

1. Create a Google Cloud project.
2. Enable the Google Calendar API.
3. Create an OAuth client for a web application.
4. Add this authorized redirect URI:

```text
https://your-render-url/api/calendar/google/callback
```

5. Put the client ID, client secret, and redirect URI into Render environment variables.

Each SignalOS user connects their own Google account from the Calendar button in the app. SignalOS uses the `https://www.googleapis.com/auth/calendar.events` scope so it can create, update, and delete events for that connected user.

## Calendar Options

SignalOS is calendar-ready in two ways:

### Free ICS Feed

Every user has a private `.ics` feed URL protected by that user's API token. Calendar apps can subscribe to this URL without Google Cloud, OAuth, or billing.

Use this when:

- you want the no-spend MVP path
- you only need SignalOS reminders to appear in a calendar
- slower calendar refresh is acceptable

Tradeoffs:

- refresh timing is controlled by the calendar app
- it is mostly one-way sync from SignalOS to calendar
- Google Calendar may refresh subscribed feeds slowly

### Optional Google Calendar Sync

When Google OAuth is configured, each user can connect their own Google account. SignalOS then creates, updates, and removes events in that user's primary calendar when reminders change.

Use this when:

- you want faster and more direct calendar updates
- each SignalOS user should connect their own calendar
- you are comfortable configuring Google Cloud OAuth

Tradeoffs:

- Google Cloud may require billing verification
- production OAuth publishing may require a Google consent-screen review depending on distribution

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

Google Calendar status:

```text
GET /api/calendar/status
```

Connect Google Calendar:

```text
GET /api/calendar/google/connect
```

Sync connected user's reminders:

```text
POST /api/calendar/google/sync
```

Setup URLs for phone, webhook, and calendar:

```text
GET /api/setup
```

## Phone And Calendar Workflow

For the MVP, the laptop runs the SignalOS server and owns the central SQLite database. Laptop browser, phone browser, and iPhone Shortcuts can all talk to the same local server when they are on the same network.

The calendar layer supports both an ICS fallback and per-user Google Calendar OAuth:

- confirmed reminders are exposed through a per-user `/api/calendar.ics?token=...` feed
- the feed can be subscribed to from calendar apps
- each logged-in user can connect their own Google Calendar account
- confirmed scheduled reminders are inserted or updated on that user's primary Google Calendar
- completing or deleting a reminder removes the linked Google Calendar event when possible
- Google credentials are stored per user and are not shared between accounts

The database includes calendar sync metadata fields:

- `calendar_provider`
- `calendar_event_id`
- `calendar_synced_at`
- `calendar_sync_status`

## Project Structure

```text
SignalOS/
|-- server.py                 # FastAPI backend, extraction logic, API routes
|-- ai_bridge.py              # Optional authenticated bridge to local Ollama
|-- start_ai_bridge.example.bat # Template for a private local bridge launcher
|-- requirements.txt          # Python dependencies
|-- render.yaml               # Render Blueprint config
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
- Add extraction regression tests and benchmark examples
- Add settings for default reminder time and notification method
- Improve phone shortcut and ICS subscription documentation
- Add UI for copying private webhook and ICS URLs from settings

Later:

- Add PWA push notifications
- Add subtasks for multi-step work
- Bring back progress bars after the presentation MVP
- Add richer Google Calendar settings such as default calendar and reminder lead time
- Add export/backup tooling for personal data

## Resume Summary

Built SignalOS, a local-first GenAI/NLP action inbox that converts messy text, Hinglish prompts, receipt-like inputs, and multi-line notes into confirmed tasks, reminders, events, and calendar-ready actions using FastAPI, SQLite/Postgres, deterministic NLP extraction, smart Ollama fallback, per-user auth, tokenized webhooks, ICS feeds, optional Google Calendar sync, and a human-in-the-loop confirmation workflow.
