#!/bin/bash
# Double-click this file to launch Expense Tracker.
# Starts the local server and opens it in your browser.

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "Setting up (first run only)..."
    python3 -m venv .venv
    ./.venv/bin/pip install -r requirements.txt
fi

PORT=8000
URL="http://127.0.0.1:$PORT"

# If something is already serving on this port, just open the browser.
if curl -s -o /dev/null "$URL"; then
    echo "Expense Tracker is already running."
    open "$URL"
    exit 0
fi

echo "Starting Expense Tracker..."
( sleep 1.5; open "$URL" ) &

# Explicit 127.0.0.1 -- never 0.0.0.0, which would expose your financial
# data to every device on the same wifi.
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
