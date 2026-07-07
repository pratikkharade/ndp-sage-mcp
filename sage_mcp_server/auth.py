"""HTTP request authentication helpers for the Sage MCP server.

Only headers + query parameters are consulted — no context-vars, no globals.
The extracted token is returned verbatim so downstream services can decide
how to use it (Basic decode, Bearer forward, ``username:token`` split, ...).
"""

from __future__ import annotations

import logging
from typing import Any, Optional


logger = logging.getLogger(__name__)


def extract_auth_from_request(request: Any) -> Optional[str]:
    """Extract an authentication token from an incoming HTTP request.

    Priority:
      1. ``Authorization: Basic ...`` — the whole header value is returned
         (callers decode base64 themselves).
      2. ``Authorization: Bearer <token>`` — the token part is returned.
      3. ``X-SAGE-Token`` header.
      4. ``?token=`` query parameter.

    Returns ``None`` when no auth is present or the request is missing
    header/query support.
    """
    if not request:
        return None
    try:
        headers = getattr(request, "headers", None)
        if headers is not None:
            auth_header = headers.get("Authorization")
            if auth_header:
                if auth_header.startswith("Basic "):
                    return auth_header
                if auth_header.startswith("Bearer "):
                    return auth_header[7:].strip() or None

            xtoken = headers.get("X-SAGE-Token")
            if xtoken:
                return xtoken

        query_params = getattr(request, "query_params", None)
        if query_params is not None:
            qp_token = query_params.get("token")
            if qp_token:
                return qp_token
    except Exception as exc:  # never let auth extraction crash a request
        logger.debug("extract_auth_from_request failed: %s", exc)

    return None
