"""End-to-end tool invocation tests with mocked network.

Uses monkeypatching to fake sage_data_client + requests so tools can run
without hitting real Sage endpoints. Exercises the FastMCP tool dispatch
path (call_tool -> tool function -> mocked backends).
"""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from sage_mcp_server.server import build_server


@pytest.fixture
def fake_query_df():
    """DataFrame shaped like sage_data_client output."""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z"]),
            "value": [22.5, 23.1],
            "name": ["env.temperature", "env.temperature"],
            "meta.vsn": ["W001", "W001"],
            "meta.sensor": ["bme680", "bme680"],
            "meta.phase": ["Production", "Production"],
            "plugin": ["registry/plugin-iio:1", "registry/plugin-iio:1"],
        }
    )


@pytest.fixture
def mcp(monkeypatch, fake_query_df):
    # Patch sage_data_client used inside the data service so no network happens
    import sage_data_client

    monkeypatch.setattr(sage_data_client, "query", lambda **kw: fake_query_df.copy())
    return build_server(enable_middleware=False)


def _call_tool(mcp, name, args=None):
    args = args or {}
    result = asyncio.run(mcp._mcp_call_tool(name, args))
    return result


def test_get_node_temperature_returns_summary(mcp):
    result = _call_tool(mcp, "get_node_temperature", {"node_id": "W001"})
    text = _text(result)
    assert "W001" in text
    assert "22" in text or "23" in text  # some numeric temperature shown


def test_get_temperature_summary(mcp):
    result = _call_tool(mcp, "get_temperature_summary", {"time_range": "-1h"})
    text = _text(result)
    assert "Temperature Summary" in text


def test_search_measurements_matches(mcp):
    result = _call_tool(mcp, "search_measurements", {"measurement_pattern": "env"})
    text = _text(result)
    assert "Found" in text or "records" in text or "measurements" in text.lower()


def test_get_environmental_summary(mcp):
    result = _call_tool(mcp, "get_environmental_summary", {"time_range": "-1h"})
    text = _text(result)
    assert "Environmental data summary" in text
    assert "W001" in text


def test_get_node_all_data(mcp):
    result = _call_tool(mcp, "get_node_all_data", {"node_id": "W001"})
    text = _text(result)
    assert "W001" in text
    assert "sensor data" in text.lower()


def test_list_available_nodes(mcp):
    result = _call_tool(mcp, "list_available_nodes")
    text = _text(result)
    assert "Available Sage Nodes" in text
    assert "W001" in text


def test_ask_sage_docs_faq(mcp):
    result = _call_tool(mcp, "sage_faq", {"topic": "getting_started"})
    text = _text(result)
    assert "Sage" in text or "get started" in text.lower()


def test_sage_faq_lists_topics_when_empty(mcp):
    result = _call_tool(mcp, "sage_faq", {"topic": ""})
    text = _text(result)
    assert "getting_started" in text


def test_get_image_proxy_url_rejects_invalid(mcp):
    result = _call_tool(mcp, "get_image_proxy_url", {"sage_url": "https://evil.example/x.jpg"})
    text = _text(result)
    assert "Invalid URL" in text


def test_get_image_proxy_url_ok(mcp, monkeypatch):
    monkeypatch.setenv("SAGE_PROXY_BASE_URL", "https://mcp.example/")
    # rebuild server so the new env is captured
    from sage_mcp_server.server import build_server as _bs

    m = _bs(enable_middleware=False)
    result = asyncio.run(
        m._mcp_call_tool(
            "get_image_proxy_url",
            {
                "sage_url": "https://storage.sagecontinuum.org/api/v1/data/x.jpg",
                "auth_token": "user:tok",
            },
        )
    )
    text = _text(result)
    assert "/proxy/image?url=" in text
    assert "user%3Atok" in text or "user:tok" in text


def _text(result):
    """Extract the plaintext body from a tool call result."""
    # FastMCP returns (content_list, structured) tuples; content items have `.text`.
    if isinstance(result, tuple):
        content = result[0]
    else:
        content = getattr(result, "content", result)
    if isinstance(content, list):
        parts = []
        for item in content:
            parts.append(getattr(item, "text", str(item)))
        return "\n".join(parts)
    return str(content)
