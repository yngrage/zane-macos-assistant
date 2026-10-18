#!/usr/bin/env bash
# Start ZANE:  bash scripts/run.sh                 interactive session
#              bash scripts/run.sh ask "..."       one request, then exit
#              bash scripts/run.sh doctor [--raw]  environment report + tool smoke test
#              bash scripts/run.sh key set|status|clear
#              bash scripts/run.sh --offline       never call a model
# Stop: type /quit or press Ctrl-D. Ctrl-C cancels the running request. There is no background service.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo "Run: bash scripts/setup.sh first." >&2
  exit 1
fi
exec .venv/bin/python -m zane "$@"
