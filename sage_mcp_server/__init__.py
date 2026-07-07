"""Sage MCP Server package.

Core components for the Sage Model Context Protocol server, exposed via
FastMCP with stdio, SSE, and streamable-HTTP transports.
"""

from .models import (
    SageConfig,
    TimeRange,
    NodeID,
    DataType,
    SelectorRequirements,
    PluginArguments,
    PluginSpec,
    SageJob,
    CameraSageJob,
)
from .utils import safe_timestamp_format, parse_time_range
from .data_service import SageDataService
from .job_service import SageJobService
from .docs_helper import SAGEDocsHelper
from .plugin_metadata import plugin_registry, PluginRegistry, PluginMetadata
from .plugin_query_service import plugin_query_service, PluginQueryService
from .plugin_generator import PluginTemplate, PluginRequirements, PluginGenerator
from .job_templates import JobTemplates
from .analytics_service import AnalyticsService, get_analytics_service
from .auth import extract_auth_from_request

__version__ = "2.0.0"
__all__ = [
    # Models
    "SageConfig",
    "TimeRange",
    "NodeID",
    "DataType",
    "SelectorRequirements",
    "PluginArguments",
    "PluginSpec",
    "SageJob",
    "CameraSageJob",
    # Utils
    "safe_timestamp_format",
    "parse_time_range",
    # Services
    "SageDataService",
    "SageJobService",
    "SAGEDocsHelper",
    # Plugin system
    "plugin_registry",
    "PluginRegistry",
    "PluginMetadata",
    "plugin_query_service",
    "PluginQueryService",
    "PluginTemplate",
    "PluginRequirements",
    "PluginGenerator",
    # Templates
    "JobTemplates",
    # Analytics
    "AnalyticsService",
    "get_analytics_service",
    # Auth
    "extract_auth_from_request",
]
