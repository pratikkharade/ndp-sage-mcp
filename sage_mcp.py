#!/usr/bin/env python3
"""Sage MCP server entrypoint.

The heavy lifting lives in the :mod:`sage_mcp_server` package. This file is
kept as a thin CLI so ``python sage_mcp.py`` continues to work exactly as it
did previously.

Transports supported (via ``MCP_TRANSPORT`` env var):
    * ``stdio``                        — default for local IDE clients
    * ``streamable-http`` / ``http``   — HTTP with SSE-style streaming (default here)
    * ``sse``                          — legacy Server-Sent Events transport

Runtime configuration is entirely env-driven so it plays well with Docker,
Kubernetes, and MCP client launchers:

    MCP_TRANSPORT   (default: streamable-http)
    MCP_HOST        (default: 0.0.0.0)
    MCP_PORT        (default: 8000)
    MCP_PATH        (default: /mcp — only used for streamable-http)
    LOG_LEVEL       (default: INFO)
    ADMIN_API_KEY   (required to hit /analytics/* endpoints)
    SAGE_USER,SAGE_PASS   (used by the image proxy for Basic auth)
    SAGE_PROXY_BASE_URL   (public URL for the /proxy/image endpoint)
"""

from __future__ import annotations

from sage_mcp_server.server import get_server, main


# expose the FastMCP instance for `fastmcp run sage_mcp.py` and similar tools
mcp = get_server()


if __name__ == "__main__":
    main()
