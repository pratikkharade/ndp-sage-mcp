#!/usr/bin/env python3
"""Composition entrypoint: Sage MCP + NDP tools.

This wires the additive NDP tools onto the Sage FastMCP instance *without
modifying anything in the ``sage_mcp_server`` package*. The Sage server is
built via its own factory; we then attach the NDP tools to the same instance
before it runs.

It works because ``get_server()`` caches a singleton: we build it, register
the NDP tools on it, and ``main()`` later re-fetches that same cached instance
— so the NDP tools are present when the transport starts.

Point your MCP launcher at this file instead of ``sage_mcp.py`` to get the
NDP tools. Configuration is unchanged (all the same env vars), plus the NDP_*
vars documented in ``ndp/README.md``.
"""

from __future__ import annotations

import logging
import os

from sage_mcp_server.server import get_server, main

logger = logging.getLogger(__name__)

# Build (and cache) the Sage server, then attach the NDP tools to it.
mcp = get_server()

try:
    import ndp

    state = getattr(mcp, "state", {}) or {}
    ndp.register(mcp, state.get("data_service"))
    logger.info(
        "NDP tools attached (target catalog=%s)", os.getenv("NDP_SERVER", "local")
    )
except Exception:  # pragma: no cover - NDP must never break the Sage server
    logger.warning("NDP tools not attached; continuing with Sage tools only", exc_info=True)


if __name__ == "__main__":
    main()
