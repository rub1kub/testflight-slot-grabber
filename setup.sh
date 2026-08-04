#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3}

if [[ $(/usr/bin/uname -s) != "Darwin" ]]; then
  echo "This project requires macOS." >&2
  exit 1
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python 3 is missing: $PYTHON_BIN" >&2
  exit 1
fi
if ! /usr/bin/xcrun --find swiftc >/dev/null 2>&1; then
  echo "Swift Command Line Tools are missing. Run: xcode-select --install" >&2
  exit 1
fi

if [[ ! -f "$PROJECT_ROOT/config.json" ]]; then
  /bin/cp "$PROJECT_ROOT/config.example.json" "$PROJECT_ROOT/config.json"
  echo "Created config.json from config.example.json (dry_run=true)."
fi

/bin/mkdir -p "$HOME/Library/Application Support/TestFlightSlotGrabber"
/bin/mkdir -p "$HOME/Library/Logs/TestFlightSlotGrabber/artifacts"
/bin/chmod +x "$PROJECT_ROOT"/*.sh "$PROJECT_ROOT"/scripts/*.sh

echo "Building native Accessibility helper..."
(cd "$PROJECT_ROOT" && /usr/bin/swift build -c release)
"$PROJECT_ROOT/scripts/build-ax-helper-app.sh" >/dev/null
"$PROJECT_ROOT/scripts/build-mock-app.sh" >/dev/null

echo "Running parser and pipeline tests..."
(cd "$PROJECT_ROOT" && "$PYTHON_BIN" -m unittest discover -s tests -v)

echo "Setup complete."
echo "Next diagnostic: $PROJECT_ROOT/diagnose.sh"
if ! "$PROJECT_ROOT/helper/TestFlightAXHelper.app/Contents/MacOS/testflight-ax" permission --json >/dev/null 2>&1; then
  echo "Accessibility is not yet granted. See README.md -> Permissions."
fi
