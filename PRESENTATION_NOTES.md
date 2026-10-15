# SignalOS MVP - Presentation Notes

## One-Line Pitch

SignalOS is a local-first NLP system that converts messy everyday messages into confirmed reminders, tasks, and events.

## Problem

People receive actionable information through noisy messages: WhatsApp texts, college groups, emails, screenshots, reminders from friends, and informal Hinglish messages. Normal reminder apps require manual entry, while chatbots require the user to ask questions. SignalOS focuses on automatic action extraction.

## Example

Input:

```text
cll me at 9 pm
```

Output:

```json
{
  "title": "Call me",
  "type": "reminder",
  "due_at": "today 9:00 PM",
  "confidence": 0.96,
  "needs_confirmation": true
}
```

The system asks the user to confirm before saving the reminder.

## MVP Features

- Paste messy natural-language text
- Import text files
- Normalize chat/OCR-style abbreviations
- Classify intent: reminder, task, event, note
- Extract action titles
- Parse dates and times, including relative expressions
- Handle basic Hinglish patterns like `kal 8 baje`
- Score priority and confidence
- Confirm before saving
- Store reminders in SQLite
- Show confirmed reminders in an action timeline
- Optional Ollama endpoint path for local Llama extraction

## NLP Stack

- Text normalization
- Intent classification
- Temporal expression parsing
- Action extraction
- Rule-based entity detection
- Priority scoring
- Structured JSON generation
- Human-in-the-loop validation

## Tech Stack

- FastAPI backend
- SQLite database
- Vanilla mobile-first frontend
- Optional Ollama/Llama local model integration
- Pydantic validation

## Why It Is Not Just A Chatbot

The user does not ask questions. The system reads an input message, extracts the action, and proposes a structured task/reminder. The main output is an action timeline, not a chat response.

## Demo Inputs

```text
cll me at 9 pm
```

```text
Submit NLP project presentation by Friday 5 pm and call mentor tomorrow morning
```

```text
kal 8 baje gym chalna
```

```text
fees last date is 20 Oct, pay before end of day
```

## Future Scope

- Screenshot OCR
- Mobile PWA install
- Telegram or WhatsApp-style forwarding
- Browser or Telegram notifications
- Scam/risk detection
- Study planner module
- Diet and health assistant module
- Local LLM personalization using Ollama

## Resume Bullet

Built SignalOS, a local-first NLP action assistant that converts noisy everyday messages into confirmed reminders, tasks, and events using FastAPI, SQLite, temporal parsing, intent classification, action extraction, structured validation, and optional Ollama/Llama-based local AI extraction.

