#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3}
cd "$PROJECT_ROOT"
"$PYTHON_BIN" -m testflight_grabber health || true

LABEL="local.testflight-slot-grabber"
if /bin/launchctl print "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null 2>&1; then
  echo "launch_agent: loaded"
else
  echo "launch_agent: not loaded"
fi
