# SignalOS

SignalOS is a local-first NLP action inbox that turns messy everyday messages into confirmed reminders, tasks, events, and important dates.

The MVP is designed around a confirmation loop: SignalOS extracts structured candidates, the user reviews or edits them, and only confirmed items are saved to the action timeline.

## Current MVP

- Dark, mobile-first web app UI
- Text capture and text-file import
- Deterministic NLP extraction for reliable fallback
- Automatic hybrid extraction with Ollama when `llama3.1:8b` is available locally
- Editable confirmation cards before saving
- Reminder, task, event, note, and important-date types
- Monthly, weekly, daily, and yearly recurrence metadata
- SQLite storage
- Timeline filters for active, recurring, completed, and all reminders
- Calendar export at `/api/calendar.ics`
- PWA manifest and service-worker shell

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

For phone testing on the same Wi-Fi, open:

```text
http://YOUR-LAPTOP-IP:8000
```

## Demo Inputs

- `cll me at 9 pm`
- `my anniversary is on th 16th of every month`
- `Submit NLP project presentation by Friday 5 pm and call mentor tomorrow morning`
- `fees last date is 20 Oct, pay before end of day`
- `kal 8 baje gym chalna and remind me to upload assignment tonight`
- `Meeting with project team tomorrow at 6:30 pm`

## Local AI

If Ollama is running with `llama3.1:8b`, SignalOS uses it automatically and validates the result with deterministic rules. If Ollama is unavailable or times out, SignalOS falls back to deterministic extraction.

```powershell
ollama pull llama3.1:8b
ollama serve
```

## Calendar Export

Confirmed scheduled reminders can be exported as an `.ics` calendar feed:

```text
http://127.0.0.1:8000/api/calendar.ics
```

This is the first bridge toward phone notifications because iPhone, Google Calendar, and other calendar apps already handle reminder alerts reliably.

## iPhone Shortcut Webhook

SignalOS can receive shared text from an iPhone Shortcut and queue extracted candidates for confirmation in the app.

Endpoint:

```text
POST http://YOUR-LAPTOP-IP:8001/api/webhook/capture
```

JSON body:

```json
{
  "text": "fees last date is 20 Oct, pay before end of day",
  "source_type": "iphone_shortcut"
}
```

The webhook does not auto-save reminders. It adds pending confirmation cards to the SignalOS UI.

## Next Work

- Add OCR for screenshots and image uploads
- Add Google Calendar API sync
- Add PWA push notifications for installed iPhone/desktop app mode
- Add extraction tests and benchmark examples
- Add a proper settings screen for default reminder time and notification method
