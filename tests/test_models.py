"""Tests for the pydantic models and utility helpers."""

from __future__ import annotations

import json

import pytest

from sage_mcp_server import (
    NodeID,
    PluginArguments,
    PluginSpec,
    SageJob,
    SelectorRequirements,
    TimeRange,
    parse_time_range,
    safe_timestamp_format,
)


class TestNodeID:
    def test_normalizes_prefix(self):
        assert NodeID(value="023").value == "W023"

    def test_uppercases_and_strips(self):
        assert NodeID(value="  w023  ").value == "W023"

    def test_leaves_prefixed_alone(self):
        assert NodeID(value="W097").value == "W097"

    def test_empty(self):
        assert NodeID(value="").value == ""

    def test_str(self):
        assert str(NodeID(value="w1")) == "W1"


class TestTimeRange:
    @pytest.mark.parametrize("v", ["latest", "recent", "current", "now", "", " "])
    def test_defaults_for_relative_keywords(self, v):
        assert TimeRange(value=v).value == "-30m"

    def test_passes_through(self):
        assert TimeRange(value="-5m").value == "-5m"


class TestSelectorRequirements:
    def test_to_dict_empty(self):
        assert SelectorRequirements().to_dict() == {}

    def test_to_dict_with_selectors(self):
        sr = SelectorRequirements(gpu=True, camera=True, usb=False)
        assert sr.to_dict() == {"resource.gpu": "true", "resource.camera": "true"}

    def test_from_json_str_preserves_custom(self):
        sr = SelectorRequirements.from_json_str(
            json.dumps({"resource.gpu": "true", "zone": "core"})
        )
        assert sr.gpu is True
        assert sr.custom_selectors == {"zone": "core"}
        assert sr.to_dict()["zone"] == "core"

    def test_from_json_str_invalid(self):
        sr = SelectorRequirements.from_json_str("not json")
        assert sr.to_dict() == {}


class TestPluginArguments:
    def test_from_json(self):
        pa = PluginArguments.from_string('{"width": 1920, "height": 1080}')
        assert pa.args_dict == {"width": 1920, "height": 1080}

    def test_from_kv(self):
        pa = PluginArguments.from_string("width=1920,height=1080")
        assert pa.args_dict == {"width": "1920", "height": "1080"}

    def test_to_cli(self):
        pa = PluginArguments(args_dict={"a": 1, "b": "two"})
        assert pa.to_cli_args() == ["--a", "1", "--b", "two"]


class TestPluginSpec:
    def test_to_dict_minimal(self):
        spec = PluginSpec(name="p", image="registry/example:1")
        out = spec.to_dict()
        assert out == {"name": "p", "pluginSpec": {"image": "registry/example:1", "volume": {}}}

    def test_to_dict_full(self):
        spec = PluginSpec(
            name="p",
            image="i",
            args=PluginArguments(args_dict={"k": "v"}),
            selector=SelectorRequirements(gpu=True),
            privileged=True,
            entrypoint="/bin/bash",
            env={"FOO": "bar"},
        )
        s = spec.to_dict()["pluginSpec"]
        assert s["args"] == ["--k", "v"]
        assert s["selector"] == {"resource.gpu": "true"}
        assert s["privileged"] is True
        assert s["entrypoint"] == "/bin/bash"
        assert s["env"] == {"FOO": "bar"}


class TestSageJob:
    def test_nodes_null_format(self):
        job = SageJob(name="j", nodes=["W1", "W2"], plugins=[])
        d = job.to_dict()
        assert d["nodes"] == {"W1": None, "W2": None}

    def test_nodes_true_format(self):
        job = SageJob(name="j", nodes=["W1"], plugins=[], node_value_format="true")
        assert job.to_dict()["nodes"] == {"W1": True}


class TestParseTimeRange:
    def test_relative_minutes(self):
        s, e = parse_time_range("-5m")
        assert s and e and s < e

    def test_relative_hours(self):
        s, e = parse_time_range("-2h")
        assert s and e

    def test_relative_seconds(self):
        s, e = parse_time_range("-30s")
        assert s and e

    def test_iso(self):
        s, e = parse_time_range("2025-01-01T00:00:00Z")
        assert s == "2025-01-01T00:00:00Z"
        assert e == "2025-01-01T01:00:00Z"

    def test_from_timerange_obj(self):
        s, e = parse_time_range(TimeRange(value="-1h"))
        assert s and e


class TestSafeTimestampFormat:
    def test_none(self):
        assert safe_timestamp_format(None) == "N/A"

    def test_string(self):
        assert safe_timestamp_format("2025-01-01T00:00:00Z") == "2025-01-01T00:00:00Z"

    def test_datetime(self):
        import datetime as _dt

        assert (
            safe_timestamp_format(_dt.datetime(2025, 1, 1, 12, 30, 45))
            == "2025-01-01T12:30:45Z"
        )
