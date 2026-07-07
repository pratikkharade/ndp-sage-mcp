"""Tests for the HTTP auth extraction helper."""

from __future__ import annotations

from types import SimpleNamespace

from sage_mcp_server.auth import extract_auth_from_request


def _req(headers=None, query=None):
    return SimpleNamespace(headers=headers or {}, query_params=query or {})


def test_returns_none_for_missing_request():
    assert extract_auth_from_request(None) is None


def test_returns_none_when_no_auth():
    assert extract_auth_from_request(_req()) is None


def test_basic_auth_returned_verbatim():
    header_val = "Basic dXNlcjpwdw=="
    assert extract_auth_from_request(_req(headers={"Authorization": header_val})) == header_val


def test_bearer_stripped():
    assert extract_auth_from_request(_req(headers={"Authorization": "Bearer abcdef"})) == "abcdef"


def test_bearer_empty_returns_none():
    assert extract_auth_from_request(_req(headers={"Authorization": "Bearer    "})) is None


def test_x_sage_token_header():
    assert extract_auth_from_request(_req(headers={"X-SAGE-Token": "user:tok"})) == "user:tok"


def test_query_param_fallback():
    assert extract_auth_from_request(_req(query={"token": "qptok"})) == "qptok"


def test_priority_authorization_over_query():
    req = _req(headers={"Authorization": "Bearer h"}, query={"token": "q"})
    assert extract_auth_from_request(req) == "h"


def test_no_query_params_ok():
    # request objects without query_params should not raise
    req = SimpleNamespace(headers={"X-SAGE-Token": "hi"})
    assert extract_auth_from_request(req) == "hi"
