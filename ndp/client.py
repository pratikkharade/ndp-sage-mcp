"""NDP client — thin wrapper over the official `ndp_ep.APIClient`.

All NDP traffic goes through here. The official client is synchronous, so
calls are offloaded to a thread to avoid blocking the MCP event loop.

Config (env):
    NDP_API_URL    base URL of the NDP endpoint API
    NDP_API_KEY    bearer token (NDP_API_TOKEN is accepted as a fallback)
    NDP_SERVER     which CKAN catalog to target: 'local' (default) or 'pre_ckan'

`NDP_SERVER` selects where registrations land. 'local' writes to this
endpoint's own catalog (via the EP-API); 'pre_ckan' writes to the public
National Data Platform catalog. It defaults to 'local' so nothing is made
public by accident — publishing to pre_ckan is a separate, explicit step.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from ndp_ep import APIClient

logger = logging.getLogger(__name__)

VALID_SERVERS = ("local", "pre_ckan")
DEFAULT_SERVER = os.getenv("NDP_SERVER", "local").strip() or "local"


def _resolve_token() -> Optional[str]:
    """Bearer token. `NDP_API_KEY` is what IDE MCP configs set; keep the older
    `NDP_API_TOKEN` name working too so existing setups don't break."""
    return os.getenv("NDP_API_KEY") or os.getenv("NDP_API_TOKEN")


class NDPError(Exception):
    """Raised when an NDP operation fails."""


class NDPClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        server: str = DEFAULT_SERVER,
    ):
        self.base_url = base_url or os.getenv("NDP_API_URL")
        self.token = token or _resolve_token()
        self.server = (server or "local").strip()

        if not self.base_url:
            raise NDPError("NDP_API_URL is not set (env or constructor argument).")
        if not self.token:
            raise NDPError(
                "No NDP token found — set NDP_API_KEY (or NDP_API_TOKEN) in the env."
            )
        if self.server not in VALID_SERVERS:
            raise NDPError(
                f"NDP_SERVER={self.server!r} is invalid; use one of {VALID_SERVERS}."
            )

        self._client = APIClient(base_url=self.base_url, token=self.token)
        logger.info(
            "NDP client ready (endpoint=%s, target catalog=%s)",
            self.base_url,
            self.server,
        )

    async def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Run a sync ndp_ep method in a thread, normalizing errors."""
        method = getattr(self._client, method_name)
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except Exception as e:  # ndp_ep raises varied exception types
            raise NDPError(f"{method_name} failed: {e}") from e

    # -- organizations ------------------------------------------------------

    async def list_organizations(self, name: Optional[str] = None) -> List[str]:
        result = await self._call("list_organizations", name=name, server=self.server)
        return result if isinstance(result, list) else []

    async def register_organization(
        self, name: str, title: str, description: str = ""
    ) -> Dict[str, Any]:
        return await self._call(
            "register_organization",
            {"name": name, "title": title, "description": description},
            server=self.server,
        )

    async def ensure_organization(
        self, name: str, title: Optional[str] = None, description: str = ""
    ) -> bool:
        """Create the org if absent. Returns True if it was created."""
        orgs = await self.list_organizations()
        if name in orgs:
            return False
        await self.register_organization(name, title or name.upper(), description)
        logger.info("Created NDP organization %r", name)
        return True

    # -- datasets -----------------------------------------------------------

    async def search_datasets(
        self, terms: List[str], keys: Optional[List[Optional[str]]] = None
    ) -> List[Dict[str, Any]]:
        result = await self._call(
            "search_datasets", terms, keys=keys, server=self.server
        )
        return result if isinstance(result, list) else []

    async def dataset_exists(self, name: str) -> bool:
        datasets = await self.search_datasets([name], keys=["name"])
        return any(d.get("name") == name for d in datasets)

    async def _activate_dataset(self, dataset_id: str, server: str) -> None:
        """Flip a freshly-created dataset from CKAN 'draft' to 'active'.

        The EP-API creates datasets in 'draft' state, which are hidden from
        package_list and search until activated. Best-effort: never fail the
        caller if the patch is rejected.
        """
        try:
            await self._call(
                "patch_general_dataset", dataset_id, {"state": "active"}, server=server
            )
        except NDPError:
            logger.warning(
                "Registered %s but could not activate it (still a draft).",
                dataset_id,
            )

    async def register_general_dataset(self, data: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(
            "Registering NDP dataset %r in org %r (%d resources, server=%s)",
            data.get("name"),
            data.get("owner_org"),
            len(data.get("resources") or []),
            self.server,
        )
        result = await self._call(
            "register_general_dataset", data, server=self.server
        )
        dataset_id = result.get("id") if isinstance(result, dict) else None
        if dataset_id:
            await self._activate_dataset(dataset_id, self.server)
        return result

    async def patch_general_dataset(
        self, dataset_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await self._call(
            "patch_general_dataset", dataset_id, data, server=self.server
        )

    async def publish_to_server(
        self, data: Dict[str, Any], server: str
    ) -> Dict[str, Any]:
        """Register a dataset against a specific catalog ('local' or 'pre_ckan')."""
        if server not in VALID_SERVERS:
            raise NDPError(f"target {server!r} is invalid; use one of {VALID_SERVERS}.")
        result = await self._call("register_general_dataset", data, server=server)
        # Same draft->active step as register_general_dataset, against the target
        # catalog — otherwise a local publish lands as an invisible draft.
        dataset_id = result.get("id") if isinstance(result, dict) else None
        if dataset_id:
            await self._activate_dataset(dataset_id, server)
        return result

    async def publish_to_pre_ckan(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Register the same dataset against the public PRE-CKAN catalog."""
        return await self.publish_to_server(data, "pre_ckan")

    # -- resources ----------------------------------------------------------

    async def register_url(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call("register_url", data, server=self.server)

    async def get_resource(self, resource_id: str) -> Dict[str, Any]:
        return await self._call("get_resource", resource_id, server=self.server)

    async def delete_dataset_resource(
        self, dataset_id: str, resource_id: str
    ) -> Dict[str, Any]:
        return await self._call(
            "delete_dataset_resource", dataset_id, resource_id, server=self.server
        )

    # -- kafka --------------------------------------------------------------

    async def register_kafka_topic(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call("register_kafka_topic", data, server=self.server)

    async def get_kafka_details(self) -> Dict[str, Any]:
        return await self._call("get_kafka_details")

    # -- misc ---------------------------------------------------------------

    async def get_user_info(self) -> Dict[str, Any]:
        return await self._call("get_user_info")

    async def get_system_status(self) -> Dict[str, Any]:
        return await self._call("get_system_status")
