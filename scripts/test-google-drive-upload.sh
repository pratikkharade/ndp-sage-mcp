#!/usr/bin/env bash
# Live smoke test for the Google Drive destination configured in .env.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

if [[ -x venv/bin/python ]]; then
    exec venv/bin/python scripts/test_google_drive_upload.py "$@"
elif [[ -x .venv/bin/python ]]; then
    exec .venv/bin/python scripts/test_google_drive_upload.py "$@"
else
    exec python3 scripts/test_google_drive_upload.py "$@"
fi
