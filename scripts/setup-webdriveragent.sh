#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
export APPIUM_HOME="$PROJECT_ROOT/.appium"

if [[ ! -d /Applications/Xcode.app ]]; then
  echo "Full Xcode is required. Install it from the Mac App Store, open it once, then rerun this script." >&2
  exit 2
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "Node.js/npm is required." >&2
  exit 2
fi

cd "$PROJECT_ROOT"
npm install --no-save --no-audit --no-fund appium@latest
if ! "$PROJECT_ROOT/node_modules/.bin/appium" driver list --installed | grep -q 'xcuitest'; then
  "$PROJECT_ROOT/node_modules/.bin/appium" driver install xcuitest
fi
echo "Appium XCUITest driver installed under $APPIUM_HOME."
echo "WebDriverAgent signing still requires selecting your Development Team in Xcode for the connected iPhone."
