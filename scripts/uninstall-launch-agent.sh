#!/bin/bash
set -euo pipefail

LABEL="local.testflight-slot-grabber"
DOMAIN="gui/$(/usr/bin/id -u)"
DESTINATION="$HOME/Library/LaunchAgents/$LABEL.plist"

if /bin/launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  /bin/launchctl bootout "$DOMAIN/$LABEL"
fi
if [[ -f "$DESTINATION" ]]; then
  /bin/mkdir -p "$HOME/.Trash"
  TRASHED="$HOME/.Trash/$LABEL.$(/bin/date +%Y%m%d-%H%M%S).plist"
  /bin/mv "$DESTINATION" "$TRASHED"
  echo "LaunchAgent unloaded; plist moved to $TRASHED"
else
  echo "LaunchAgent is not installed."
fi
