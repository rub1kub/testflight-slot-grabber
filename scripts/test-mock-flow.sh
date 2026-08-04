#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
HELPER="$PROJECT_ROOT/helper/TestFlightAXHelper.app/Contents/MacOS/testflight-ax"
MOCK_APP="$PROJECT_ROOT/mock/TestFlightAXMock.app"

"$PROJECT_ROOT/scripts/build-mock-app.sh" >/dev/null
open "$MOCK_APP"
for _ in {1..30}; do
  STATUS=$("$HELPER" status --json --allow-mock --process-name testflight-ax-mock 2>/dev/null || true)
  if [[ "$STATUS" == *'"accept_button":true'* ]]; then
    break
  fi
  /bin/sleep 0.1
done
"$HELPER" inspect --json --allow-mock --process-name testflight-ax-mock
"$HELPER" accept --json --allow-mock --process-name testflight-ax-mock --timeout 3
"$HELPER" install --json --allow-mock --process-name testflight-ax-mock --timeout 3
"$HELPER" status --json --allow-mock --process-name testflight-ax-mock
/usr/bin/killall testflight-ax-mock >/dev/null 2>&1 || true
