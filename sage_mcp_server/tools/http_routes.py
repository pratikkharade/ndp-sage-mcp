"""Custom HTTP routes: analytics REST API + image proxy."""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.parse
from typing import Optional

import httpx
from starlette.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..analytics_service import AnalyticsService
from ..auth import extract_auth_from_request


logger = logging.getLogger(__name__)


def verify_admin_api_key(request) -> bool:
    admin_key = os.getenv("ADMIN_API_KEY")
    if not admin_key:
        logger.warning("ADMIN_API_KEY not set — analytics endpoints will reject all requests")
        return False

    provided = request.headers.get("X-Admin-API-Key")
    if provided and provided == admin_key:
        return True

    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer ") and auth[7:] == admin_key:
        return True

    if request.query_params.get("api_key") == admin_key:
        return True

    return False


def _json(payload) -> Response:
    return JSONResponse(payload)


def register(mcp, *, analytics_service: AnalyticsService, proxy_base_url: Optional[str] = None):
    proxy_base_url = proxy_base_url or os.getenv(
        "SAGE_PROXY_BASE_URL", "http://localhost:8000"
    ).rstrip("/")

    # --------------------------------------------------------------
    # health / readiness
    # --------------------------------------------------------------
    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request):
        return _json({"status": "ok"})

    # --------------------------------------------------------------
    # analytics
    # --------------------------------------------------------------
    @mcp.custom_route("/analytics/summary", methods=["GET"])
    async def analytics_summary(request):
        try:
            analytics_service.track_request(request, endpoint="/analytics/summary", method="GET")
        except Exception:
            pass
        if not verify_admin_api_key(request):
            raise StarletteHTTPException(status_code=401, detail="Invalid or missing admin API key")
        return _json(analytics_service.get_analytics_summary())

    @mcp.custom_route("/analytics/users", methods=["GET"])
    async def analytics_users(request):
        if not verify_admin_api_key(request):
            raise StarletteHTTPException(status_code=401, detail="Invalid or missing admin API key")
        return _json(analytics_service.get_user_stats())

    @mcp.custom_route("/analytics/tools", methods=["GET"])
    async def analytics_tools(request):
        if not verify_admin_api_key(request):
            raise StarletteHTTPException(status_code=401, detail="Invalid or missing admin API key")
        return _json(analytics_service.get_tool_stats())

    @mcp.custom_route("/analytics/user/{user_id}", methods=["GET"])
    async def analytics_user(request):
        if not verify_admin_api_key(request):
            raise StarletteHTTPException(status_code=401, detail="Invalid or missing admin API key")
        user_id = request.path_params.get("user_id")
        if not user_id:
            raise StarletteHTTPException(status_code=400, detail="user_id is required")
        users = analytics_service.get_user_stats()
        info = next((u for u in users if u["user_id"] == user_id), None)
        if not info:
            raise StarletteHTTPException(status_code=404, detail=f"User '{user_id}' not found")
        return _json({"user_info": info, "tool_usage": analytics_service.get_user_tool_usage(user_id)})

    @mcp.custom_route("/analytics/activity", methods=["GET"])
    async def analytics_activity(request):
        if not verify_admin_api_key(request):
            raise StarletteHTTPException(status_code=401, detail="Invalid or missing admin API key")
        try:
            limit = int(request.query_params.get("limit", "50"))
        except ValueError:
            raise StarletteHTTPException(status_code=400, detail="Invalid limit parameter")
        limit = max(1, min(limit, 1000))
        return _json(analytics_service.get_recent_activity(limit=limit))

    # --------------------------------------------------------------
    # image proxy
    # --------------------------------------------------------------
    @mcp.custom_route("/proxy/image", methods=["GET"])
    async def proxy_image(request):
        try:
            url = request.query_params.get("url")
            if not url:
                raise StarletteHTTPException(status_code=400, detail="Missing required parameter: url")
            if not url.startswith("https://storage.sagecontinuum.org/"):
                raise StarletteHTTPException(
                    status_code=400, detail="Invalid URL: Only Sage storage URLs are allowed"
                )

            headers = {}
            sage_user = os.getenv("SAGE_USER")
            sage_pass = os.getenv("SAGE_PASS")
            incoming_auth = request.headers.get("Authorization")
            token = request.query_params.get("token") or extract_auth_from_request(request)

            if sage_user and sage_pass:
                credentials = base64.b64encode(f"{sage_user}:{sage_pass}".encode()).decode()
                headers["Authorization"] = f"Basic {credentials}"
            elif incoming_auth and incoming_auth.startswith("Bearer "):
                bearer = incoming_auth[7:].strip()
                if ":" in bearer:
                    creds = base64.b64encode(bearer.encode()).decode()
                    headers["Authorization"] = f"Basic {creds}"
                else:
                    headers["Authorization"] = f"Bearer {bearer}"
            elif incoming_auth:
                headers["Authorization"] = incoming_auth
            elif token:
                if ":" in token:
                    creds = base64.b64encode(token.encode()).decode()
                    headers["Authorization"] = f"Basic {creds}"
                else:
                    headers["Authorization"] = f"Bearer {token}"

            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()

            content_type = response.headers.get("content-type", "application/octet-stream")
            if content_type == "application/octet-stream":
                lower = url.lower()
                if lower.endswith((".jpg", ".jpeg")):
                    content_type = "image/jpeg"
                elif lower.endswith(".png"):
                    content_type = "image/png"
                elif lower.endswith(".gif"):
                    content_type = "image/gif"
                elif lower.endswith(".webp"):
                    content_type = "image/webp"

            return Response(
                content=response.content,
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=3600", "X-Sage-Proxy": "true"},
            )
        except StarletteHTTPException:
            raise
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            detail = f"Upstream Sage response ({status}): {e.response.text[:500]}"
            raise StarletteHTTPException(status_code=status, detail=detail)
        except httpx.RequestError as e:
            raise StarletteHTTPException(status_code=502, detail=f"Network error: {e}")
        except Exception as e:
            logger.error(f"Error proxying image: {e}")
            raise StarletteHTTPException(status_code=500, detail=str(e))

    # --------------------------------------------------------------
    # get_image_proxy_url as MCP tool
    # --------------------------------------------------------------
    @mcp.tool
    def get_image_proxy_url(sage_url: str, auth_token: str = "") -> str:
        """Build a proxy URL for a Sage image accessible via this server.

        Never embeds a hard-coded fallback token — if neither ``auth_token`` nor
        ``SAGE_USER``/``SAGE_PASS`` are set, returns an unauthenticated URL.
        """
        if not sage_url.startswith("https://storage.sagecontinuum.org/"):
            return "Error: Invalid URL. Only Sage storage URLs are supported."

        encoded_url = urllib.parse.quote(sage_url, safe="")

        if not auth_token:
            sage_user = os.getenv("SAGE_USER")
            sage_pass = os.getenv("SAGE_PASS")
            if sage_user and sage_pass:
                auth_token = f"{sage_user}:{sage_pass}"

        query = f"url={encoded_url}"
        if auth_token:
            query += f"&token={urllib.parse.quote(auth_token, safe='')}"
        proxy_url = f"{proxy_base_url}/proxy/image?{query}"

        lines = [
            "Sage Image Proxy URL:",
            "",
            proxy_url,
            "",
        ]
        if not auth_token:
            lines.extend(
                [
                    "Note: no auth_token was provided and SAGE_USER/SAGE_PASS were not set;",
                    "protected images will require authentication when fetched.",
                ]
            )
        else:
            lines.append("Authentication is embedded in the URL — usable directly with curl/browser.")
        return "\n".join(lines)
