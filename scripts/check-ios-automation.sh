#!/bin/bash
set -u

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
READY=1

if [[ -d /Applications/Xcode.app ]]; then
  echo "xcode: installed"
  /usr/bin/xcodebuild -version 2>/dev/null | /usr/bin/head -n 2
else
  echo "xcode: missing (only Command Line Tools are currently selected)"
  READY=0
fi

if [[ -x "$PROJECT_ROOT/node_modules/.bin/appium" ]]; then
  echo "appium: $($PROJECT_ROOT/node_modules/.bin/appium --version 2>/dev/null)"
else
  echo "appium: missing (optional; run setup-webdriveragent.sh after Xcode is installed)"
  READY=0
fi

if [[ -d /Applications/Xcode.app ]]; then
  DEVICES=$(/usr/bin/xcrun xcdevice list 2>/dev/null || true)
  if echo "$DEVICES" | /usr/bin/grep -q '"platform" : "com.apple.platform.iphoneos"'; then
    echo "iphone: detected"
  else
    echo "iphone: not detected"
    READY=0
  fi
else
  echo "iphone: cannot query without full Xcode"
  READY=0
fi

if [[ $READY -eq 1 ]]; then
  echo "ios_automation: ready for signing verification"
  exit 0
fi
echo "ios_automation: not ready"
exit 1
