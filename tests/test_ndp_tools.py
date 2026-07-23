"""Focused tests for the additive NDP FastMCP tools."""

from __future__ import annotations

import asyncio

import pandas as pd
from fastmcp import FastMCP

from ndp.registration import scan_path
from ndp.tools import register


class FakeNDPClient:
    server = "local"

    def __init__(self):
        self.general_payload = None
        self.url_payload = None
        self.search_results = []
        self.patch_dataset_id = None
        self.patch_payload = None

    async def register_general_dataset(self, payload):
        self.general_payload = payload
        return {"id": "dataset-id"}

    async def register_url(self, payload):
        self.url_payload = payload
        return {"id": "legacy-id"}

    async def search_datasets(self, terms, keys=None):
        return self.search_results

    async def patch_general_dataset(self, dataset_id, payload):
        self.patch_dataset_id = dataset_id
        self.patch_payload = payload
        return {"id": dataset_id}


def _text(result) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, list):
        return "\n".join(getattr(item, "text", str(item)) for item in content)
    return str(content)


def _server(client):
    mcp = FastMCP("ndp-test")
    register(mcp, data_service=None, client=client)
    return mcp


def test_register_url_supports_distinct_dataset_and_resource_names():
    client = FakeNDPClient()
    mcp = _server(client)

    result = asyncio.run(
        mcp.call_tool(
            "ndp_register_url",
            {
                "resource_url": "https://example.com/test",
                "name": "test dataset",
                "owner_org": "test-organization",
                "resource_name": "test resource",
                "confirm": True,
            },
        )
    )

    assert client.url_payload is None
    assert client.general_payload == {
        "name": "test-dataset",
        "title": "test dataset",
        "owner_org": "test-organization",
        "resources": [
            {
                "url": "https://example.com/test",
                "name": "test resource",
            }
        ],
        "private": False,
    }
    assert "test resource" in _text(result)
    assert "test-dataset" in _text(result)


def test_register_url_rejects_non_http_url_before_writing():
    client = FakeNDPClient()
    mcp = _server(client)

    result = asyncio.run(
        mcp.call_tool(
            "ndp_register_url",
            {
                "resource_url": "test url",
                "name": "test dataset",
                "owner_org": "test-organization",
                "resource_name": "test resource",
                "confirm": True,
            },
        )
    )

    assert client.general_payload is None
    assert client.url_payload is None
    assert "must be an absolute HTTP(S) URL" in _text(result)


def test_register_url_preserves_legacy_endpoint_when_resource_name_is_omitted():
    client = FakeNDPClient()
    mcp = _server(client)

    asyncio.run(
        mcp.call_tool(
            "ndp_register_url",
            {
                "resource_url": "https://example.com/test",
                "name": "test dataset",
                "owner_org": "test-organization",
                "confirm": True,
            },
        )
    )

    assert client.general_payload is None
    assert client.url_payload == {
        "resource_name": "test-dataset",
        "resource_title": "test dataset",
        "owner_org": "test-organization",
        "resource_url": "https://example.com/test",
    }


def test_local_file_resource_keeps_absolute_path(tmp_path):
    source = tmp_path / "measurements.csv"
    source.write_text("timestamp,value\n2026-01-01T00:00:00Z,1\n")

    resources, questions, warnings = scan_path(str(source), mode="single")

    assert not questions
    assert not warnings
    assert len(resources) == 1
    assert resources[0].url == str(source.resolve())
    assert resources[0].format == "CSV"


def test_append_from_sage_adds_only_new_beehive_resources():
    old_url = "https://storage.sagecontinuum.org/old.jpg"
    new_url = "https://storage.sagecontinuum.org/new.jpg"
    client = FakeNDPClient()
    client.search_results = [
        {
            "id": "dataset-id",
            "name": "images",
            "notes": "Existing notes.",
            "resources": [{"url": old_url, "name": "old.jpg"}],
        }
    ]

    class FakeDataService:
        @staticmethod
        def query_data(start, end, filters, max_records=1000):
            return pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(
                        ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"]
                    ),
                    "value": [old_url, new_url],
                    "meta.vsn": ["W001", "W001"],
                    "name": ["upload", "upload"],
                }
            )

    mcp = FastMCP("ndp-append-test")
    register(mcp, data_service=FakeDataService(), client=client)
    result = asyncio.run(
        mcp.call_tool(
            "ndp_append_from_sage",
            {
                "dataset_name": "images",
                "time_range": "-2d",
                "node_ids": "W001",
                "measurement": "upload",
                "description_append": "Added recent images.",
                "confirm": True,
            },
        )
    )

    assert client.patch_dataset_id == "dataset-id"
    assert len(client.patch_payload["resources"]) == 1
    assert client.patch_payload["resources"][0]["url"] == new_url
    assert client.patch_payload["notes"] == (
        "Existing notes.\n\nAdded recent images."
    )
    assert "Appended 1 resource" in _text(result)
