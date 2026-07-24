#!/usr/bin/env bash
# Run the Sage MCP server locally over streamable-HTTP, bound to localhost.
#
# Useful for development, curl-based smoke testing, and any MCP client that
# connects to a URL (rather than spawning a subprocess).
#
# Usage:
#   ./scripts/run-local-http.sh                  # http://127.0.0.1:8000/mcp
#   MCP_PORT=9000 ./scripts/run-local-http.sh    # override port

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# Force sane local defaults, but let the environment/.env override.
export MCP_TRANSPORT="${MCP_TRANSPORT:-streamable-http}"
export MCP_HOST="${MCP_HOST:-127.0.0.1}"
export MCP_PORT="${MCP_PORT:-8000}"
export MCP_PATH="${MCP_PATH:-/mcp}"

echo "Sage MCP server → http://${MCP_HOST}:${MCP_PORT}${MCP_PATH}"
echo "  (Ctrl+C to stop)"
echo

if [[ -x venv/bin/python ]]; then
    exec venv/bin/python sage_mcp.py
elif [[ -x .venv/bin/python ]]; then
    exec .venv/bin/python sage_mcp.py
else
    exec python3 sage_mcp.py
fi
