#!/usr/bin/env bash
# One-time setup. Creates .venv inside this folder, installs the single dependency, creates .env from the
# placeholder template, and prints an environment report. Installs nothing system-wide and never uses sudo.
set -euo pipefail
cd "$(dirname "$0")/.."
chmod +x scripts/*.sh 2>/dev/null || true   # some unzip tools drop the executable bit

PY="${ZANE_PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.12 python3.11 python3.13 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "No python3 found. Install Python 3.10+ (for example: brew install python@3.12) and re-run." >&2
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python 3.9+ is required; found: $("$PY" --version 2>&1)." >&2
  exit 1
fi
echo "Using $("$PY" --version) at $(command -v "$PY")"

if [ -x .venv/bin/python ]; then
  echo ".venv already exists - reusing it."
else
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from the template (placeholders only)."
fi

echo
.venv/bin/python -m zane doctor || true
echo
echo "Next:"
echo "  bash scripts/run.sh key set     # store your Anthropic API key in the macOS Keychain (optional)"
echo "  bash scripts/run.sh             # start ZANE   (stop: /quit or Ctrl-D)"
echo "  bash scripts/run.sh doctor --raw   # environment report with raw macOS command output"
