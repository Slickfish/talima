#!/bin/sh
# W2 — build NEXUS's MCP bundle with the repo's interpreter.
# scripts/build_bundle.py says what it does; its arguments pass through
# (--check, --twice, --write-icon).
cd "$(dirname "$0")/.." || exit 1
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
exec "$PY" scripts/build_bundle.py "$@"
