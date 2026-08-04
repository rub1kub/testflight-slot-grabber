#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
BINARY="$PROJECT_ROOT/.build/release/testflight-ax-mock"
DESTINATION="$PROJECT_ROOT/mock/TestFlightAXMock.app/Contents/MacOS/testflight-ax-mock"

if [[ ! -x "$BINARY" ]]; then
  echo "Build the Swift package first: swift build -c release" >&2
  exit 1
fi

/bin/cp "$BINARY" "$DESTINATION"
/bin/chmod 755 "$DESTINATION"
/usr/bin/codesign --force --sign - "$PROJECT_ROOT/mock/TestFlightAXMock.app" >/dev/null
echo "$PROJECT_ROOT/mock/TestFlightAXMock.app"
