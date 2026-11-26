#!/bin/sh
set -eu
cd "$(dirname "$0")"

if [ -x .venv/bin/python ]; then
    runner=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
    runner=python3
else
    printf '%s\n' 'Python 3.10+ is required. Install Python, then run sh start.sh again.' >&2
    exit 1
fi

if ! "$runner" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    printf '%s\n' 'This project requires Python 3.10 or newer.' >&2
    exit 1
fi
exec "$runner" -B -X utf8 lab.py "$@"
