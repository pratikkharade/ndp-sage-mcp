"""Tests for AnalyticsService."""

from __future__ import annotations

from types import SimpleNamespace

from sage_mcp_server.analytics_service import AnalyticsService


def test_track_request_creates_user():
    svc = AnalyticsService()
    uid = svc.track_request(SimpleNamespace(headers={"X-SAGE-Token": "alice:xyz"}, query_params={}))
    assert uid == "alice"
    summary = svc.get_analytics_summary()
    assert summary["total_unique_users"] == 1
    assert summary["total_requests"] == 1


def test_track_tool_use_aggregates():
    svc = AnalyticsService()
    svc.track_tool_use("get_node_info", user_id="alice", success=True)
    svc.track_tool_use("get_node_info", user_id="alice", success=False)
    svc.track_tool_use("list_all_nodes", user_id="bob", success=True)
    tools = {t["tool_name"]: t for t in svc.get_tool_stats()}
    assert tools["get_node_info"]["total_uses"] == 2
    assert tools["get_node_info"]["successful_uses"] == 1
    assert tools["list_all_nodes"]["unique_users"] == 1


def test_recent_activity_reverses_order():
    svc = AnalyticsService()
    for i in range(5):
        svc.track_tool_use(f"tool_{i}", user_id="alice")
    recent = svc.get_recent_activity(3)
    assert [r["tool_name"] for r in recent] == ["tool_4", "tool_3", "tool_2"]


def test_anonymous_when_no_identity():
    svc = AnalyticsService()
    uid = svc.track_request(SimpleNamespace(headers={}, query_params={}))
    assert uid == "anonymous"


def test_user_tool_usage_sorted_desc():
    svc = AnalyticsService()
    for _ in range(3):
        svc.track_tool_use("a", user_id="alice")
    svc.track_tool_use("b", user_id="alice")
    usage = svc.get_user_tool_usage("alice")
    assert usage[0]["tool_name"] == "a"
    assert usage[0]["count"] == 3
