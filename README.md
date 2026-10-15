# SignalOS MVP

SignalOS converts messy daily messages into confirmed reminders, tasks, and events.

## Run

```powershell
cd "C:\Users\Satish batra\Documents\Codex\2026-10-13\i-x20\outputs\signalos-mvp"
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
- `Submit NLP project presentation by Friday 5 pm and call mentor tomorrow morning`
- `fees last date is 20 Oct, pay before end of day`
- `kal 8 baje gym chalna and remind me to upload assignment tonight`
- `Meeting with project team tomorrow at 6:30 pm`

## NLP Modules In This MVP

- text normalization
- intent classification
- temporal parsing
- action extraction
- priority scoring
- structured JSON output
- human confirmation loop

## Optional Ollama

If Ollama is running with `llama3.1:8b`, enable "Try Ollama first" in the UI. If Ollama is unavailable, the app falls back to deterministic extraction.

