#!/bin/bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3}
cd "$PROJECT_ROOT"

if [[ $# -eq 0 ]]; then
  set -- monitor
fi
exec "$PYTHON_BIN" -m testflight_grabber "$@"
