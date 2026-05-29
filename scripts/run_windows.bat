@echo off
REM trispoke-outbound — double-click launcher (Windows).
REM Starts Ollama (if missing), the IMAP poller, sender_loop, and the
REM Streamlit UI in separate console windows, then opens the browser.

setlocal
cd /d "%~dp0\.."

echo ===============================
echo trispoke-outbound launcher
echo project: %CD%
echo ===============================

REM --- 1. Ollama --------------------------------------------------------------
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if errorlevel 1 (
    where ollama >NUL 2>&1
    if errorlevel 1 (
        echo [warn] ollama not on PATH — local-mode generation will fail.
    ) else (
        echo Starting Ollama...
        start "Ollama" cmd /k "ollama serve"
        timeout /t 3 /nobreak >NUL
    )
) else (
    echo [ok] Ollama already running.
)

REM --- 2. Apollo events poller (V1.5) -----------------------------------------
echo Starting Apollo events poller...
start "trispoke Apollo events" cmd /k "uv run python -m trispoke.receiver.apollo_events_poller"
REM Legacy IMAP poller (only needed for SMTP-direct campaigns) — uncomment if used:
REM start "trispoke IMAP poller" cmd /k "uv run python -m trispoke.receiver.imap_poller"

REM --- 3. Apollo push worker (V1.5) -------------------------------------------
echo Starting Apollo push worker...
start "trispoke Apollo push" cmd /k "uv run python -m trispoke.sender.apollo_push_worker"
REM Legacy SMTP sender loop (only needed for SMTP-direct campaigns) — uncomment if used:
REM start "trispoke sender" cmd /k "uv run python -m trispoke.sender.sender_loop"

REM --- 3b. Pipeline runner (V1.5.2) -------------------------------------------
echo Starting pipeline runner (auto enrich/pain/generate/QC for new leads)...
start "trispoke pipeline" cmd /k "uv run python -m trispoke.scheduler.pipeline_runner"

REM --- 4. Streamlit UI --------------------------------------------------------
echo Starting Streamlit UI on http://localhost:8501 ...
start "trispoke UI" cmd /k "uv run streamlit run src/trispoke/ui/app.py --server.port 8501"

REM --- 5. Open browser --------------------------------------------------------
timeout /t 5 /nobreak >NUL
start "" "http://localhost:8501"

echo.
echo All processes launched in separate windows.
echo Stop everything with: scripts\stop.ps1
endlocal
