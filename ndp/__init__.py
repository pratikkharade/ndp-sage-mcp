"""NDP (National Data Platform) extension for the Sage MCP server."""

from .client import NDPClient, NDPError
from .tools import register

__all__ = ["NDPClient", "NDPError", "register"]