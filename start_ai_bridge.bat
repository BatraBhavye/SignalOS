@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

if not exist ".env.bridge.local" (
  echo Missing .env.bridge.local
  echo.
  echo Copy .env.bridge.example to .env.bridge.local and set a private AI_BRIDGE_TOKEN.
  echo Do not commit .env.bridge.local.
  echo.
  pause
  exit /b 1
)

for /f "usebackq tokens=1,* delims==" %%A in (".env.bridge.local") do (
  set "bridge_key=%%A"
  set "bridge_value=%%B"
  if not "!bridge_key!"=="" if not "!bridge_key:~0,1!"=="#" set "!bridge_key!=!bridge_value!"
)

if "%AI_BRIDGE_TOKEN%"=="" (
  echo AI_BRIDGE_TOKEN is missing in .env.bridge.local
  pause
  exit /b 1
)

if "%AI_BRIDGE_TOKEN%"=="replace-with-a-random-token-at-least-32-characters" (
  echo Replace the placeholder AI_BRIDGE_TOKEN in .env.bridge.local first.
  pause
  exit /b 1
)

echo Starting SignalOS AI bridge on http://127.0.0.1:8787
echo Keep this window open while you want hosted SignalOS to use local Ollama.
echo.

python -m uvicorn ai_bridge:app --host 127.0.0.1 --port 8787

echo.
echo SignalOS AI bridge stopped.
pause
