#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
LABEL="local.testflight-slot-grabber"
DOMAIN="gui/$(/usr/bin/id -u)"
DESTINATION="$HOME/Library/LaunchAgents/$LABEL.plist"
TEMPLATE="$PROJECT_ROOT/launchd/$LABEL.plist.template"
LOG_DIR="$HOME/Library/Logs/TestFlightSlotGrabber"
PID_FILE="$HOME/Library/Application Support/TestFlightSlotGrabber/monitor.pid"

if [[ ! -x "$PROJECT_ROOT/helper/TestFlightAXHelper.app/Contents/MacOS/testflight-ax" ]]; then
  echo "Run ./setup.sh first." >&2
  exit 1
fi

/bin/mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
if /bin/launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  /bin/launchctl bootout "$DOMAIN/$LABEL"
  # bootout can return before launchd has fully removed a long-running service.
  # Waiting here avoids a transient bootstrap error 5 on an immediate reinstall.
  for _ in {1..50}; do
    if ! /bin/launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      break
    fi
    /bin/sleep 0.1
  done
fi

/usr/bin/sed \
  -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
  -e "s|__LOG_DIR__|$LOG_DIR|g" \
  "$TEMPLATE" > "$DESTINATION"
/usr/bin/plutil -lint "$DESTINATION"
/bin/launchctl bootstrap "$DOMAIN" "$DESTINATION"
/bin/launchctl enable "$DOMAIN/$LABEL"
/bin/launchctl kickstart -k "$DOMAIN/$LABEL"
READY=0
for _ in {1..600}; do
  if [[ -s "$PID_FILE" ]]; then
    PID=$(/bin/cat "$PID_FILE")
    if [[ "$PID" =~ ^[0-9]+$ ]] && /bin/kill -0 "$PID" 2>/dev/null; then
      READY=1
      break
    fi
  fi
  /bin/sleep 0.1
done
if [[ $READY -ne 1 ]]; then
  echo "LaunchAgent was loaded but the monitor did not become ready within 60 seconds." >&2
  exit 1
fi
echo "Installed and started $DESTINATION"
