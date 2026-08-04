#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3}
TEST_ROOT=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/tfsg-full-pipeline.XXXXXX")

export TESTFLIGHT_DATA_DIR="$TEST_ROOT/data"
export TESTFLIGHT_LOG_DIR="$TEST_ROOT/logs"
# The target is the local mock process, so exercise real AXPress even when the
# public example config keeps external actions in dry-run mode by default.
export TESTFLIGHT_DRY_RUN=0

cd "$PROJECT_ROOT"
"$PYTHON_BIN" -m testflight_grabber monitor \
  --once \
  --fixture "$PROJECT_ROOT/tests/fixtures/available.html" \
  --mock-automation

"$PYTHON_BIN" - "$TEST_ROOT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
state_path = root / "data/state.json"
events_path = root / "logs/events.jsonl"
state = json.loads(state_path.read_text(encoding="utf-8"))
events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
required = {
    "availability_confirmed",
    "availability_notification_queued",
    "acceptance_pipeline_started",
    "ax_command_started",
    "accept_pressed",
    "install_completed",
    "ax_artifacts_captured",
    "acceptance_pipeline_completed",
    "availability_pipeline_finished",
}
seen = {event.get("event") for event in events}
missing = sorted(required - seen)
accepted = bool(state.get("accepted"))
result = state.get("last_acceptance_result", {})

sequence_values = [event.get("sequence") for event in events]
sequence_ok = sorted(sequence_values) == list(range(1, len(events) + 1))
session_ids = {event.get("session_id") for event in events}

def ids(event_name, field):
    return [event.get(field) for event in events if event.get("event") == event_name]

availability_ids = ids("availability_confirmation", "availability_id")
availability_id = availability_ids[0] if len(availability_ids) == 1 else None
correlated_availability = bool(availability_id) and all(
    event.get("availability_id") == availability_id
    for event in events
    if event.get("event") in {
        "availability_confirmation",
        "availability_confirmed",
        "availability_notification_queued",
        "availability_pipeline_finished",
    }
)
correlated_trigger = all(
    event.get("trigger_id") == availability_id
    for event in events
    if event.get("event") in {
        "acceptance_pipeline_started",
        "accept_pressed",
        "install_completed",
        "acceptance_pipeline_completed",
    }
)

attempt_ids = ids("acceptance_pipeline_started", "attempt_id")
attempt_id = attempt_ids[0] if len(attempt_ids) == 1 else None
correlated_attempt = bool(attempt_id) and all(
    event.get("attempt_id") == attempt_id
    for event in events
    if event.get("event") in {
        "acceptance_pipeline_started",
        "accept_pressed",
        "install_completed",
        "acceptance_pipeline_completed",
        "mock_app_terminated",
    }
)

ax_started = sorted(ids("ax_command_started", "ax_call_id"))
ax_completed = sorted(ids("ax_command_completed", "ax_call_id"))
notification_started = sorted(ids("notification_started", "notification_id"))
notification_finished = sorted(ids("notification_dispatch_finished", "notification_id"))
channel_events = {
    "macos_notification_completed",
    "notification_failed",
    "notification_sound_started",
    "notification_sound_failed",
    "telegram_notification_completed",
    "telegram_notification_failed",
    "telegram_notification_skipped",
}
channel_ids_present = all(
    bool(event.get("notification_id"))
    for event in events
    if event.get("event") in channel_events
)

sensitive_parts = ("authorization", "cookie", "password", "secret", "token", "chat_id")
unredacted_sensitive_fields = []
def inspect_sensitive(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            normalized = str(key).casefold().replace("-", "_")
            if any(part in normalized for part in sensitive_parts):
                if child != "<redacted>":
                    unredacted_sensitive_fields.append(child_path)
            else:
                inspect_sensitive(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            inspect_sensitive(child, f"{path}[{index}]")

for event in events:
    inspect_sensitive(event)

audit_ok = all(
    (
        sequence_ok,
        len(session_ids) == 1,
        correlated_availability,
        correlated_trigger,
        correlated_attempt,
        bool(ax_started) and ax_started == ax_completed,
        bool(notification_started) and notification_started == notification_finished,
        channel_ids_present,
        not unredacted_sensitive_fields,
    )
)
ok = accepted and bool(result.get("success")) and not missing and audit_ok
print(json.dumps({
    "ok": ok,
    "accepted_in_isolated_mock_state": accepted,
    "pipeline_success": bool(result.get("success")),
    "required_events_missing": missing,
    "audit": {
        "sequence_contiguous": sequence_ok,
        "single_session": len(session_ids) == 1,
        "availability_correlated": correlated_availability and correlated_trigger,
        "attempt_correlated": correlated_attempt,
        "ax_calls_correlated": bool(ax_started) and ax_started == ax_completed,
        "notifications_correlated": bool(notification_started) and notification_started == notification_finished and channel_ids_present,
        "unredacted_sensitive_fields": unredacted_sensitive_fields,
    },
    "events_recorded": len(events),
    "artifacts_root": str(root),
    "events_log": str(events_path),
    "state": str(state_path),
}, ensure_ascii=False, indent=2))
raise SystemExit(0 if ok else 1)
PY
