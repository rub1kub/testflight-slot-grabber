#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
APPIUM="$PROJECT_ROOT/node_modules/.bin/appium"
export APPIUM_HOME="$PROJECT_ROOT/.appium"
PORT=${APPIUM_PORT:-4723}
LOG_DIR="$HOME/Library/Logs/TestFlightSlotGrabber"

if [[ ! -x "$APPIUM" ]]; then
  echo "Appium is missing. Run scripts/setup-webdriveragent.sh first." >&2
  exit 2
fi
if [[ -z ${IOS_UDID:-} ]]; then
  echo "Set IOS_UDID to the connected iPhone identifier." >&2
  exit 2
fi

/bin/mkdir -p "$LOG_DIR"
"$APPIUM" --address 127.0.0.1 --port "$PORT" >"$LOG_DIR/appium.log" 2>&1 &
APPIUM_PID=$!
trap '/bin/kill "$APPIUM_PID" 2>/dev/null || true' EXIT
for _ in {1..40}; do
  if /usr/bin/curl -fsS "http://127.0.0.1:$PORT/status" >/dev/null 2>&1; then break; fi
  /bin/sleep 0.25
done

set +e
/usr/bin/python3 "$PROJECT_ROOT/scripts/ios_flow.py" \
  --server "http://127.0.0.1:$PORT" --udid "$IOS_UDID" "$@"
STATUS=$?
set -e
exit "$STATUS"
