#!/bin/sh
# Shared launcher for the identifier gate hooks: find a Python 3.10+ and run the gate.
# Usage: _gate.sh <mode> [args...]   (stdin is passed through for pre-push)
export PYTHONUTF8=1
root=$(git rev-parse --show-toplevel) || exit 2
for py in python3 python py; do
  if command -v "$py" >/dev/null 2>&1 &&
    "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1 </dev/null; then
    exec "$py" "$root/tools/identifier_gate.py" "$@"
  fi
done
echo "identifier-gate: ERROR: no Python 3.10+ found (tried python3, python, py); failing closed." >&2
exit 2
