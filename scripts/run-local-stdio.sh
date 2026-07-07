#!/usr/bin/env bash
# Run the Sage MCP server locally over stdio.
#
# This is the mode IDE clients (Cursor, Claude Desktop) expect when they
# spawn the server as a subprocess — nothing is bound to a network port.
#
# Usage:
#   ./scripts/run-local-stdio.sh
#
# To wire this into an IDE, point its MCP config at the absolute path of
# this script (see README section "Local: stdio for IDE clients").

set -euo pipefail

# Move to the repo root so relative paths (docs/, sage_mcp_server/) resolve.
cd "$(dirname "$0")/.."

# Load .env if the user made one (optional — variables can also be set
# directly by the parent process, which is how IDE MCP configs typically
# pass credentials).
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

export MCP_TRANSPORT=stdio

exec python3 sage_mcp.py
