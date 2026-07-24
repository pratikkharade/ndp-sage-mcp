#!/usr/bin/env bash
# Run the Sage MCP server + NDP tools locally over stdio.
#
# Identical to run-local-stdio.sh, but launches the composition entrypoint
# (sage_ndp_mcp.py) so the additive NDP tools are attached. The Sage package
# itself is not modified.
#
# Wire this into an IDE by pointing its MCP config at the absolute path of
# this script.

set -euo pipefail

# Move to the repo root so relative paths (docs/, sage_mcp_server/, ndp/) resolve.
cd "$(dirname "$0")/.."

# Load .env if present (optional — the parent process/IDE config can also set
# variables directly, which is how MCP launchers usually pass credentials).
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

export MCP_TRANSPORT=stdio

if [[ -x venv/bin/python ]]; then
  exec venv/bin/python sage_ndp_mcp.py
elif [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python sage_ndp_mcp.py
else
  exec python3 sage_ndp_mcp.py
fi
