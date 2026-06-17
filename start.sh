#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install -r requirements.txt

if [ "${1:-}" = "--setup-only" ]; then
  exit 0
fi

echo
echo "Shared Workspace MCP Server"
echo "URL: http://localhost:8765/sse"
echo "Stop: Ctrl+C"
echo

exec .venv/bin/python server.py
