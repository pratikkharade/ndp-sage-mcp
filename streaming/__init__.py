"""SciDX streaming extension for the Sage MCP server.

Adds derived-stream tools (filter the live SAGE feed into a private Kafka topic,
register it at NDP, consume it, tear it down) alongside the Sage and NDP tools.
Purely additive.
"""

from .client import StreamingError, StreamingRuntime
from .tools import register

__all__ = ["StreamingError", "StreamingRuntime", "register"]
