#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")" && pwd)
LABEL="local.testflight-slot-grabber"
DOMAIN="gui/$(/usr/bin/id -u)"
PID_FILE="$HOME/Library/Application Support/TestFlightSlotGrabber/monitor.pid"

if /bin/launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  /bin/launchctl kill SIGTERM "$DOMAIN/$LABEL" || true
fi

if [[ -f "$PID_FILE" ]]; then
  PID=$(/bin/cat "$PID_FILE")
  if [[ "$PID" =~ ^[0-9]+$ ]] && /bin/kill -0 "$PID" 2>/dev/null; then
    COMMAND=$(/bin/ps -p "$PID" -o command= || true)
    if [[ "$COMMAND" == *"$PROJECT_ROOT"* || "$COMMAND" == *"testflight_grabber"* ]]; then
      /bin/kill -TERM "$PID"
      echo "Sent SIGTERM to monitor PID $PID."
      exit 0
    fi
    echo "Refusing to stop unrelated PID $PID: $COMMAND" >&2
    exit 2
  fi
fi
echo "Monitor is not running."
