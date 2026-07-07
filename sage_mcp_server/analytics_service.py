"""Lightweight in-memory analytics tracking for the Sage MCP server.

Tracks:
  * per-user request/tool activity
  * per-tool usage counts and success rates
  * a bounded ring buffer of recent activity events

The service is intentionally in-memory only (no external DB required). It is
thread-safe via a single lock. If the process is restarted, history is lost.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class UserRecord:
    user_id: str
    first_seen: str = field(default_factory=_now_iso)
    last_seen: str = field(default_factory=_now_iso)
    total_requests: int = 0
    tool_usage: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "total_requests": self.total_requests,
            "tool_count": sum(self.tool_usage.values()),
        }


@dataclass
class ToolRecord:
    tool_name: str
    total_uses: int = 0
    successful_uses: int = 0
    users: set = field(default_factory=set)
    first_used: Optional[str] = None
    last_used: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "total_uses": self.total_uses,
            "successful_uses": self.successful_uses,
            "unique_users": len(self.users),
            "first_used": self.first_used,
            "last_used": self.last_used,
        }


class AnalyticsService:
    """Threadsafe in-memory analytics store."""

    def __init__(self, activity_buffer: int = 5000) -> None:
        self._lock = threading.Lock()
        self._users: Dict[str, UserRecord] = {}
        self._tools: Dict[str, ToolRecord] = {}
        self._activity: Deque[Dict[str, Any]] = deque(maxlen=activity_buffer)
        self._total_requests = 0

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _identify(request: Any) -> str:
        """Try to derive a stable user id from the incoming request.

        Order of preference:
          1. ``Authorization`` header ``Basic``/``Bearer`` username portion
          2. ``X-SAGE-Token`` header
          3. ``?token=`` query param
          4. request client host
          5. ``anonymous``
        """
        if request is None:
            return "anonymous"
        try:
            headers = getattr(request, "headers", {}) or {}
            auth = headers.get("Authorization") if hasattr(headers, "get") else None
            if auth:
                if auth.lower().startswith("basic "):
                    import base64

                    try:
                        decoded = base64.b64decode(auth.split(" ", 1)[1]).decode()
                        return decoded.split(":", 1)[0] or "anonymous"
                    except Exception:
                        return "basic-user"
                if auth.lower().startswith("bearer "):
                    token = auth.split(" ", 1)[1]
                    return token.split(":", 1)[0] if ":" in token else token[:12]
            xtoken = headers.get("X-SAGE-Token") if hasattr(headers, "get") else None
            if xtoken:
                return xtoken.split(":", 1)[0] if ":" in xtoken else xtoken[:12]

            qp = getattr(request, "query_params", None)
            if qp is not None:
                token = qp.get("token") if hasattr(qp, "get") else None
                if token:
                    return token.split(":", 1)[0] if ":" in token else token[:12]

            client = getattr(request, "client", None)
            if client and getattr(client, "host", None):
                return f"ip:{client.host}"
        except Exception:  # never let analytics take down a request
            logger.debug("analytics: failed to identify user", exc_info=True)
        return "anonymous"

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def track_request(
        self,
        request: Any,
        *,
        endpoint: str = "",
        method: str = "GET",
        user_id: Optional[str] = None,
    ) -> str:
        uid = user_id or self._identify(request)
        now = _now_iso()
        with self._lock:
            self._total_requests += 1
            user = self._users.setdefault(uid, UserRecord(user_id=uid))
            user.total_requests += 1
            user.last_seen = now
            self._activity.append(
                {
                    "user_id": uid,
                    "endpoint": endpoint,
                    "method": method,
                    "timestamp": now,
                    "kind": "request",
                }
            )
        return uid

    def track_tool_use(
        self,
        tool_name: str,
        *,
        user_id: str = "anonymous",
        success: bool = True,
        error_message: Optional[str] = None,
    ) -> None:
        now = _now_iso()
        with self._lock:
            user = self._users.setdefault(user_id, UserRecord(user_id=user_id))
            user.last_seen = now
            user.tool_usage[tool_name] = user.tool_usage.get(tool_name, 0) + 1

            tool = self._tools.setdefault(tool_name, ToolRecord(tool_name=tool_name))
            tool.total_uses += 1
            if success:
                tool.successful_uses += 1
            tool.users.add(user_id)
            tool.first_used = tool.first_used or now
            tool.last_used = now

            self._activity.append(
                {
                    "user_id": user_id,
                    "tool_name": tool_name,
                    "timestamp": now,
                    "success": success,
                    "error_message": error_message,
                    "kind": "tool",
                }
            )

    # ------------------------------------------------------------------
    # reporting
    # ------------------------------------------------------------------
    def get_analytics_summary(self) -> Dict[str, Any]:
        with self._lock:
            most_active_user = None
            if self._users:
                most_active_user = max(
                    self._users.values(), key=lambda u: u.total_requests
                ).user_id
            most_used_tool = None
            if self._tools:
                most_used_tool = max(
                    self._tools.values(), key=lambda t: t.total_uses
                ).tool_name
            return {
                "total_unique_users": len(self._users),
                "total_requests": self._total_requests,
                "total_tool_uses": sum(t.total_uses for t in self._tools.values()),
                "most_active_user": most_active_user,
                "most_used_tool": most_used_tool,
            }

    def get_user_stats(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [u.to_dict() for u in self._users.values()]

    def get_tool_stats(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [t.to_dict() for t in self._tools.values()]

    def get_user_tool_usage(self, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            user = self._users.get(user_id)
            if not user:
                return []
            return [
                {"tool_name": name, "count": count}
                for name, count in sorted(user.tool_usage.items(), key=lambda kv: -kv[1])
            ]

    def get_recent_activity(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, self._activity.maxlen or limit))
        with self._lock:
            # deque preserves insertion order; grab the tail
            return list(self._activity)[-limit:][::-1]


_service: Optional[AnalyticsService] = None
_service_lock = threading.Lock()


def get_analytics_service() -> AnalyticsService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = AnalyticsService()
    return _service
