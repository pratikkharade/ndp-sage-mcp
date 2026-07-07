"""Smoke tests for the FastMCP server: registration + transport wiring."""

from __future__ import annotations

import asyncio

import pytest

from sage_mcp_server.server import build_server, _resolve_transport


EXPECTED_TOOLS = {
    "get_node_all_data",
    "get_node_iio_data",
    "get_environmental_summary",
    "list_available_nodes",
    "search_measurements",
    "get_node_temperature",
    "get_temperature_summary",
    "get_node_info",
    "list_all_nodes",
    "get_sensor_details",
    "submit_sage_job",
    "check_job_status",
    "query_job_data",
    "force_remove_job",
    "suspend_job",
    "submit_plugin_job",
    "submit_multi_plugin_job",
    "get_nodes_by_location",
    "get_measurement_stat_by_location",
    "find_plugins_for_task",
    "get_plugin_data",
    "get_cloud_images",
    "get_image_data",
    "query_plugin_data_nl",
    "create_plugin",
    "ask_sage_docs",
    "sage_faq",
    "search_sage_docs",
    "get_image_proxy_url",
}


@pytest.fixture(scope="module")
def mcp():
    return build_server(enable_middleware=False)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if asyncio.get_event_loop().is_running() else asyncio.run(coro)


def test_all_expected_tools_registered(mcp):
    tools = asyncio.run(mcp.get_tools())
    missing = EXPECTED_TOOLS - set(tools.keys())
    assert not missing, f"Missing tools: {sorted(missing)}"


def test_resources_registered(mcp):
    resources = asyncio.run(mcp.get_resources())
    assert "stats://temperature" in resources
    assert "query://plugin-iio" in resources


def test_prompts_registered(mcp):
    prompts = asyncio.run(mcp.get_prompts())
    expected = {
        "summarize_temperature_anomalies",
        "suggest_image_sampler_cron",
        "suggest_environmental_job",
        "getting_started_guide",
        "plugin_development_guide",
        "data_analysis_guide",
        "troubleshooting_guide",
    }
    assert expected.issubset(set(prompts.keys()))


def test_http_app_is_asgi(mcp):
    app = mcp.http_app()
    assert callable(app), "streamable-http ASGI app must be callable"


def test_sse_app_is_asgi(mcp):
    app = mcp.sse_app()
    assert callable(app), "SSE ASGI app must be callable"


def test_transport_aliases():
    assert _resolve_transport("http") == "streamable-http"
    assert _resolve_transport("HTTP") == "streamable-http"
    assert _resolve_transport("stream") == "streamable-http"
    assert _resolve_transport("sse") == "sse"
    assert _resolve_transport("stdio") == "stdio"
    with pytest.raises(ValueError):
        _resolve_transport("xyz")
