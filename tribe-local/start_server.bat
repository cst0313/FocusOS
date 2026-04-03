@echo off
REM start_server.bat – FocusOS local inference server launcher (Windows)
REM
REM Run this file once to start the local inference server.
REM The browser extension will connect to it automatically when Tracking is ON.

echo Starting FocusOS local inference server on http://127.0.0.1:8787 ...
python -m uvicorn app:app --host 127.0.0.1 --port 8787 --reload
pause
