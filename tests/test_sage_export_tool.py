"""Tests for the general Sage query-to-CSV export tool."""

from __future__ import annotations

import asyncio

import pandas as pd
from fastmcp import FastMCP

from sage_mcp_server.tools import sensor_tools


def _text(result) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, list):
        return "\n".join(getattr(item, "text", str(item)) for item in content)
    return str(content)


def test_export_sage_query_csv_with_aggregation(monkeypatch, tmp_path):
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"]
            ),
            "value": [1.0, 2.0],
            "name": ["env.air_quality.conc", "env.air_quality.conc"],
            "meta.vsn": ["W045", "W045"],
        }
    )
    captured = {}

    def fake_query(**kwargs):
        captured.update(kwargs)
        return frame.copy()

    monkeypatch.setattr(sensor_tools.sage_data_client, "query", fake_query)
    output = tmp_path / "export.csv"
    mcp = FastMCP("export-test")
    sensor_tools.register(mcp, data_service=object())

    result = asyncio.run(
        mcp.call_tool(
            "export_sage_query_csv",
            {
                "output_path": str(output),
                "time_range": "-3d",
                "node_id": "W045",
                "measurement": "env.air_quality.conc",
                "aggregate": "mean",
                "aggregate_window": "1h",
            },
        )
    )

    assert output.exists()
    assert len(pd.read_csv(output)) == 2
    assert captured["filter"] == {
        "vsn": "W045",
        "name": "env.air_quality.conc",
    }
    assert captured["experimental_func"] == "mean"
    assert captured["experimental_window"] == "1h"
    assert "Exported 2 Sage record" in _text(result)
