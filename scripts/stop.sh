#!/usr/bin/env bash
# trispoke-outbound — kill all components (POSIX).

set -e

PATTERNS='trispoke\.receiver\.imap_poller|trispoke\.sender\.sender_loop|src/trispoke/ui/app\.py'

# Don't match this script itself.
PIDS=$(pgrep -f "$PATTERNS" 2>/dev/null | grep -v "^$$\$" || true)

if [ -z "$PIDS" ]; then
    echo "No trispoke processes running."
    exit 0
fi

for pid in $PIDS; do
    cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "(unknown)")
    echo "Stopping PID $pid: $cmd"
    kill "$pid" 2>/dev/null || echo "  (already gone)"
done

# Also stop the tmux session if present.
if command -v tmux &> /dev/null; then
    if tmux has-session -t trispoke 2>/dev/null; then
        echo "Killing tmux session 'trispoke'"
        tmux kill-session -t trispoke
    fi
fi
