"""FastMCP server factory for the Sage MCP server.

Constructs a FastMCP instance, registers all tools/resources/prompts, and
exposes helpers for running under stdio / streamable-HTTP / SSE transports.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext

from .analytics_service import AnalyticsService, get_analytics_service
from .auth import extract_auth_from_request
from .data_service import SageDataService
from .docs_helper import SAGEDocsHelper
from .job_service import SageJobService
from .models import SageConfig
from .tools import docs_tools, geo_tools, http_routes, job_tools, plugin_tools, prompts, sensor_tools


logger = logging.getLogger(__name__)


class AuthLoggingMiddleware(Middleware):
    """Middleware that logs which auth method (if any) was used on each request."""

    def __init__(self, analytics: Optional[AnalyticsService] = None) -> None:
        self.analytics = analytics

    async def on_request(self, context: MiddlewareContext, call_next):
        request = getattr(context, "request", None)
        try:
            if request is not None:
                token = extract_auth_from_request(request)
                if token:
                    logger.debug("Request authenticated (method detected)")
                if self.analytics is not None:
                    try:
                        self.analytics.track_request(
                            request,
                            endpoint=str(getattr(request, "url", "")),
                            method=getattr(request, "method", "GET"),
                        )
                    except Exception:  # analytics must not fail requests
                        logger.debug("analytics tracking failed", exc_info=True)
        except Exception:
            logger.debug("Auth middleware inspection failed", exc_info=True)
        return await call_next(context)


def build_server(
    *,
    name: str = "SageDataMCP",
    sage_config: Optional[SageConfig] = None,
    analytics: Optional[AnalyticsService] = None,
    docs_file_path: str = "docs/llms.md",
    proxy_base_url: Optional[str] = None,
    enable_middleware: bool = True,
) -> FastMCP:
    """Build and return a fully configured FastMCP server."""

    sage_config = sage_config or SageConfig()
    analytics = analytics or get_analytics_service()

    mcp = FastMCP(name)

    data_service = SageDataService()
    job_service = SageJobService(sage_config)
    docs_helper = SAGEDocsHelper(docs_file_path=docs_file_path)

    if enable_middleware:
        mcp.add_middleware(AuthLoggingMiddleware(analytics=analytics))

    # --------------------------------------------------------------
    # tool registration
    # --------------------------------------------------------------
    sensor_tools.register(mcp, data_service=data_service)
    job_tools.register(
        mcp,
        data_service=data_service,
        job_service=job_service,
        find_plugins_for_task=plugin_tools.find_plugins_for_task_impl,
    )
    geo_tools.register(mcp, data_service=data_service)
    plugin_tools.register(mcp, data_service=data_service)
    docs_tools.register(mcp, docs_helper=docs_helper)
    prompts.register(mcp)
    http_routes.register(mcp, analytics_service=analytics, proxy_base_url=proxy_base_url)

    # attach for introspection/testing
    mcp.state = {  # type: ignore[attr-defined]
        "data_service": data_service,
        "job_service": job_service,
        "docs_helper": docs_helper,
        "analytics": analytics,
        "sage_config": sage_config,
    }
    return mcp


# lazy singleton for the default server
_default_server: Optional[FastMCP] = None


def get_server() -> FastMCP:
    global _default_server
    if _default_server is None:
        _default_server = build_server()
    return _default_server


# --------------------------------------------------------------------------
# CLI entrypoint
# --------------------------------------------------------------------------
def _configure_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


TRANSPORT_ALIASES = {
    "http": "streamable-http",
    "streaming": "streamable-http",
    "stream": "streamable-http",
    "streamable": "streamable-http",
    "streamable-http": "streamable-http",
    "sse": "sse",
    "stdio": "stdio",
}


def _resolve_transport(name: str) -> str:
    key = name.lower().strip()
    if key not in TRANSPORT_ALIASES:
        raise ValueError(
            f"Unknown transport '{name}'. Choose from: stdio, streamable-http (a.k.a. http), sse."
        )
    return TRANSPORT_ALIASES[key]


def main() -> None:
    _configure_logging()
    mcp = get_server()

    transport = _resolve_transport(os.getenv("MCP_TRANSPORT", "streamable-http"))
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))
    path = os.getenv("MCP_PATH", "/mcp")

    logger.info("Starting Sage MCP server with transport=%s", transport)
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    if host == "0.0.0.0":
        logger.warning("MCP_HOST=0.0.0.0 — server is exposed on all interfaces")

    kwargs: dict[str, Any] = {"host": host, "port": port, "log_level": os.getenv("LOG_LEVEL", "info").lower()}
    if transport == "streamable-http":
        kwargs["path"] = path
    mcp.run(transport=transport, **kwargs)
