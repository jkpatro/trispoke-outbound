#!/usr/bin/env bash
# trispoke-outbound — double-clickable macOS launcher.
# Spawns separate Terminal windows for each component via osascript.

set -e
cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"

echo "==============================="
echo "trispoke-outbound launcher"
echo "project: $PROJECT_DIR"
echo "==============================="

new_window() {
    local title="$1"
    local cmd="$2"
    osascript <<EOF
tell application "Terminal"
    do script "cd '$PROJECT_DIR' && echo '--- $title ---' && $cmd"
    activate
end tell
EOF
}

# --- 1. Ollama ----------------------------------------------------------------
if pgrep -x "ollama" > /dev/null; then
    echo "[ok] Ollama already running."
else
    if command -v ollama > /dev/null; then
        echo "Starting Ollama..."
        new_window "Ollama" "ollama serve"
        sleep 3
    else
        echo "[warn] ollama not on PATH — local-mode generation will fail."
    fi
fi

# --- 2. Apollo events poller (V1.5) ------------------------------------------
echo "Starting Apollo events poller..."
new_window "Apollo events" "uv run python -m trispoke.receiver.apollo_events_poller"
# Legacy IMAP poller (only for SMTP-direct campaigns):
# new_window "IMAP poller" "uv run python -m trispoke.receiver.imap_poller"

# --- 3. Apollo push worker (V1.5) --------------------------------------------
echo "Starting Apollo push worker..."
new_window "Apollo push" "uv run python -m trispoke.sender.apollo_push_worker"
# Legacy SMTP sender loop (only for SMTP-direct campaigns):
# new_window "Sender" "uv run python -m trispoke.sender.sender_loop"

# --- 4. Streamlit UI ----------------------------------------------------------
echo "Starting Streamlit UI on http://localhost:8501 ..."
new_window "UI" "uv run streamlit run src/trispoke/ui/app.py --server.port 8501"

# --- 5. Open browser ----------------------------------------------------------
sleep 5
open "http://localhost:8501"

echo
echo "All processes launched in separate Terminal windows."
echo "Stop everything with: bash scripts/stop.sh"
