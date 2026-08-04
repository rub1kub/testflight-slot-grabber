#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
USER_DOMAIN="gui/$(/usr/bin/id -u)"
ARTIFACT_ROOT=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/tfsg-launchd-e2e.XXXXXX")
LABEL="local.testflight-slot-grabber.e2e.$(/bin/date +%s).$$"
STDOUT_LOG="$ARTIFACT_ROOT/stdout.log"
STDERR_LOG="$ARTIFACT_ROOT/stderr.log"
SUBMITTED=0

cleanup_job() {
  if [[ $SUBMITTED -eq 1 ]]; then
    /bin/launchctl remove "$LABEL" >/dev/null 2>&1 || true
  fi
}
trap cleanup_job EXIT

/bin/launchctl submit \
  -l "$LABEL" \
  -o "$STDOUT_LOG" \
  -e "$STDERR_LOG" \
  -- "$PROJECT_ROOT/scripts/test-full-pipeline.sh"
SUBMITTED=1

SUCCEEDED=0
for _ in {1..600}; do
  SERVICE=$(/bin/launchctl print "$USER_DOMAIN/$LABEL" 2>/dev/null || true)
  if [[ -s "$STDOUT_LOG" ]] \
    && /usr/bin/grep -q '"ok": true' "$STDOUT_LOG" \
    && /usr/bin/grep -q 'last exit code = 0' <<<"$SERVICE"; then
    SUCCEEDED=1
    break
  fi
  if /usr/bin/grep -Eq 'last exit code = [1-9][0-9]*' <<<"$SERVICE"; then
    break
  fi
  /bin/sleep 0.1
done

/usr/bin/printf 'launchd_artifact_root=%s\n' "$ARTIFACT_ROOT"
if [[ -s "$STDOUT_LOG" ]]; then
  /bin/cat "$STDOUT_LOG"
fi
if [[ -s "$STDERR_LOG" ]]; then
  /bin/cat "$STDERR_LOG" >&2
fi

if [[ $SUCCEEDED -ne 1 ]]; then
  echo "launchd end-to-end pipeline did not complete successfully" >&2
  exit 1
fi
