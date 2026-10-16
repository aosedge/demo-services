#!/usr/bin/env bash
# Single entry point. Reads config.env, runs the suite, prints a PASS/FAIL summary.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

if [ ! -f config.env ]; then
    echo "config.env is missing. Copy config.example.env to config.env and fill it in." >&2
    exit 2
fi

selector="${1:-bootstrap or cloud}"
exec python3 -m pytest -m "$selector" \
    --cov=runner --cov-report=xml:coverage.xml --cov-report=term-missing
