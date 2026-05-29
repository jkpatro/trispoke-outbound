#!/usr/bin/env bash
# trispoke-outbound — Linux launcher.
# Detects the available terminal (gnome-terminal, xterm) or falls back to tmux.

set -e
cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"

echo "==============================="
echo "trispoke-outbound launcher"
echo "project: $PROJECT_DIR"
echo "==============================="

USE_TMUX=0
TERM_LAUNCH=""

if command -v gnome-terminal &> /dev/null; then
    TERM_LAUNCH="gnome-terminal --title"
elif command -v konsole &> /dev/null; then
    TERM_LAUNCH="konsole --new-tab -p tabtitle"
elif command -v xterm &> /dev/null; then
    TERM_LAUNCH="xterm -T"
elif command -v tmux &> /dev/null; then
    USE_TMUX=1
    SESSION="trispoke"
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        echo "tmux session '$SESSION' already exists — attach with: tmux attach -t $SESSION"
        exit 0
    fi
    tmux new-session -d -s "$SESSION" -n main
else
    cat <<EOF >&2
No supported terminal emulator found, and tmux not installed.
Run components manually in four shells from $PROJECT_DIR:
  ollama serve
  uv run python -m trispoke.receiver.imap_poller
  uv run python -m trispoke.sender.sender_loop
  uv run streamlit run src/trispoke/ui/app.py
Then open http://localhost:8501
EOF
    exit 1
fi

start_window() {
    local title="$1"
    local cmd="$2"
    if [ "$USE_TMUX" = "1" ]; then
        tmux new-window -t "$SESSION" -n "$title" "bash -lc 'cd \"$PROJECT_DIR\" && $cmd; exec bash'"
    else
        $TERM_LAUNCH "$title" -- bash -c "cd '$PROJECT_DIR' && $cmd; exec bash" &
    fi
}

# --- 1. Ollama ----------------------------------------------------------------
if pgrep -x "ollama" > /dev/null; then
    echo "[ok] Ollama already running."
else
    if command -v ollama > /dev/null; then
        echo "Starting Ollama..."
        start_window "Ollama" "ollama serve"
        sleep 3
    else
        echo "[warn] ollama not on PATH — local-mode generation will fail."
    fi
fi

# --- 2. Apollo events poller (V1.5) ------------------------------------------
start_window "Apollo events" "uv run python -m trispoke.receiver.apollo_events_poller"
# Legacy: start_window "IMAP" "uv run python -m trispoke.receiver.imap_poller"

# --- 3. Apollo push worker (V1.5) --------------------------------------------
start_window "Apollo push" "uv run python -m trispoke.sender.apollo_push_worker"
# Legacy: start_window "Sender" "uv run python -m trispoke.sender.sender_loop"

# --- 3b. Pipeline runner (V1.5.2) --------------------------------------------
start_window "Pipeline" "uv run python -m trispoke.scheduler.pipeline_runner"

# --- 4. Streamlit UI ----------------------------------------------------------
start_window "UI" "uv run streamlit run src/trispoke/ui/app.py --server.port 8501"

# --- 5. Open browser ----------------------------------------------------------
sleep 5
if command -v xdg-open &> /dev/null; then
    xdg-open "http://localhost:8501" &
fi

echo
if [ "$USE_TMUX" = "1" ]; then
    echo "All processes launched in tmux session '$SESSION'."
    echo "Attach with: tmux attach -t $SESSION"
else
    echo "All processes launched in separate terminal windows."
fi
echo "Stop everything with: bash scripts/stop.sh"
