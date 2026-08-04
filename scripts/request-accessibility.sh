#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
HELPER="$PROJECT_ROOT/helper/TestFlightAXHelper.app/Contents/MacOS/testflight-ax"
if [[ ! -x "$HELPER" ]]; then
  echo "Run ./setup.sh first." >&2
  exit 1
fi
"$HELPER" prompt-permission --json || true
open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'
