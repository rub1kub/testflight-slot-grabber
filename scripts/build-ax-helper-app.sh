#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
BINARY="$PROJECT_ROOT/.build/release/testflight-ax"
APP="$PROJECT_ROOT/helper/TestFlightAXHelper.app"
DESTINATION="$APP/Contents/MacOS/testflight-ax"

if [[ ! -x "$BINARY" ]]; then
  echo "Build the Swift package first: swift build -c release" >&2
  exit 1
fi

# An ad-hoc signature is content-addressed. Re-signing after a real binary
# change requires macOS to approve the new code requirement, but rebuilding an
# identical binary must not disturb an already approved helper. Compare the
# unsigned payload and verify the existing bundle before replacing anything.
if [[ -x "$DESTINATION" ]] && /usr/bin/codesign --verify --strict "$APP" >/dev/null 2>&1; then
  COMPARISON_DIR=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/tfsg-signature-check.XXXXXX")
  CANDIDATE_APP="$COMPARISON_DIR/TestFlightAXHelper.app"
  cleanup_comparison() {
    /bin/rm -f "$CANDIDATE_APP/Contents/_CodeSignature/CodeResources"
    /bin/rm -f "$CANDIDATE_APP/Contents/MacOS/testflight-ax"
    /bin/rm -f "$CANDIDATE_APP/Contents/Info.plist"
    /bin/rmdir "$CANDIDATE_APP/Contents/_CodeSignature" 2>/dev/null || true
    /bin/rmdir "$CANDIDATE_APP/Contents/MacOS" 2>/dev/null || true
    /bin/rmdir "$CANDIDATE_APP/Contents" 2>/dev/null || true
    /bin/rmdir "$CANDIDATE_APP" 2>/dev/null || true
    /bin/rmdir "$COMPARISON_DIR" 2>/dev/null || true
  }
  trap cleanup_comparison EXIT
  /bin/mkdir -p "$CANDIDATE_APP/Contents/MacOS"
  /bin/cp "$APP/Contents/Info.plist" "$CANDIDATE_APP/Contents/Info.plist"
  /bin/cp "$BINARY" "$CANDIDATE_APP/Contents/MacOS/testflight-ax"
  /bin/chmod 755 "$CANDIDATE_APP/Contents/MacOS/testflight-ax"
  /usr/bin/codesign --force --sign - --identifier local.testflight-slot-grabber.axhelper "$CANDIDATE_APP" >/dev/null 2>&1
  CURRENT_CDHASH=$(/usr/bin/codesign -dvvv "$APP" 2>&1 | /usr/bin/awk -F= '/^CDHash=/{print $2}')
  CANDIDATE_CDHASH=$(/usr/bin/codesign -dvvv "$CANDIDATE_APP" 2>&1 | /usr/bin/awk -F= '/^CDHash=/{print $2}')
  if [[ -n "$CURRENT_CDHASH" && "$CURRENT_CDHASH" = "$CANDIDATE_CDHASH" ]]; then
    echo "$APP (unchanged; existing signature preserved)"
    exit 0
  fi
fi

/bin/cp "$BINARY" "$DESTINATION"
/bin/chmod 755 "$DESTINATION"
/usr/bin/codesign --force --sign - --identifier local.testflight-slot-grabber.axhelper "$APP" >/dev/null
echo "$APP"
