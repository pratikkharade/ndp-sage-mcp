"""NDP MCP tools.

Registered onto the existing FastMCP instance alongside the Sage tools.
Purely additive — nothing in the Sage-side modules is modified.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Literal, Optional, Union

from . import registration as reg_core
from . import staging
from .client import NDPClient, NDPError
from .models import RegistrationPreview, RegistrationResult
from .registration import beehive_resources, sage_provenance, scan_path

logger = logging.getLogger(__name__)


def _resolve_parse_time_range():
    """Find the Sage `parse_time_range` helper, wherever the package sits.

    Falls back to a local implementation if the import layout differs, so the
    NDP tools never fail to load because of a path assumption.
    """
    for module in ("sage_mcp_server.utils", "..utils", "utils"):
        try:
            if module.startswith("."):
                from importlib import import_module

                mod = import_module(module, package=__package__)
            else:
                from importlib import import_module

                mod = import_module(module)
            if hasattr(mod, "parse_time_range"):
                return mod.parse_time_range
        except Exception:
            continue

    def _fallback(time_range):
        from datetime import datetime, timedelta, timezone

        text = str(time_range).strip()
        if text.startswith("-"):
            unit = text[-1]
            try:
                amount = int(text[1:-1])
            except ValueError:
                amount = 30
                unit = "m"
            delta = {
                "m": timedelta(minutes=amount),
                "h": timedelta(hours=amount),
                "d": timedelta(days=amount),
            }.get(unit, timedelta(minutes=30))
            end = datetime.now(timezone.utc)
            return (
                (end - delta).strftime("%Y-%m-%dT%H:%M:%SZ"),
                end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        return text, None

    logger.warning("Sage parse_time_range not found; using local fallback.")
    return _fallback


def register(mcp, data_service, client: Optional[NDPClient] = None) -> None:
    """Attach NDP tools to `mcp`.

    `data_service` is the existing SageDataService (used read-only).
    """
    _client_holder: Dict[str, Any] = {"client": client}
    parse_time_range = _resolve_parse_time_range()

    def _ndp() -> NDPClient:
        if _client_holder["client"] is None:
            _client_holder["client"] = NDPClient()
        return _client_holder["client"]

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @mcp.tool
    async def ndp_list_organizations() -> str:
        """List organizations available in the NDP catalog.

        Use this to pick a valid `owner_org` before registering a dataset.
        """
        try:
            orgs = await _ndp().list_organizations()
        except NDPError as e:
            return f"Error listing organizations: {e}"
        if not orgs:
            return "No organizations found."
        head = orgs[:60]
        out = [f"NDP organizations ({len(orgs)} total):"]
        out += [f"- {o}" for o in head]
        if len(orgs) > len(head):
            out.append(f"... and {len(orgs) - len(head)} more")
        return "\n".join(out)

    @mcp.tool
    async def ndp_search_datasets(terms: str, limit: int = 10) -> str:
        """Search the NDP catalog. `terms` is a space-separated keyword string."""
        term_list = [t for t in terms.split() if t]
        if not term_list:
            return "Provide at least one search term."
        try:
            results = await _ndp().search_datasets(term_list)
        except NDPError as e:
            return f"Error searching NDP: {e}"

        items = results if isinstance(results, list) else results.get("datasets", [])
        if not items:
            return f"No datasets found for: {' '.join(term_list)}"

        out = [f"Found {len(items)} dataset(s) for {' '.join(term_list)!r}:"]
        for item in items[:limit]:
            if isinstance(item, dict):
                out.append(
                    f"- {item.get('title') or item.get('name')} "
                    f"[{item.get('name')}] org={item.get('owner_org', '?')} "
                    f"resources={len(item.get('resources', []))}"
                )
            else:
                out.append(f"- {item}")
        return "\n".join(out)

    # ------------------------------------------------------------------
    # Organizations — write
    # ------------------------------------------------------------------

    @mcp.tool
    async def ndp_create_organization(
        name: str,
        title: str = "",
        description: str = "",
        confirm: bool = False,
    ) -> str:
        """Create an organization in the NDP catalog.

        Organizations own datasets, so you need one before registering a dataset
        under it. Idempotent: if the organization already exists, nothing is
        created. Writes to the configured catalog (NDP_SERVER). Requires
        confirm=True.
        """
        ndp = _ndp()
        slug = reg_core.slugify(name)
        if not confirm:
            return (
                f"This will create organization {slug!r} (title: {title or slug!r}) "
                f"in the {ndp.server} catalog. Call again with confirm=True to proceed."
            )
        try:
            created = await ndp.ensure_organization(slug, title or slug, description)
        except NDPError as e:
            return f"Could not create organization {slug!r}: {e}"
        if created:
            return f"Created organization {slug!r} in the {ndp.server} catalog."
        return (
            f"Organization {slug!r} already exists in the {ndp.server} catalog; "
            "nothing to do."
        )

    # ------------------------------------------------------------------
    # Registration — arbitrary URL
    # ------------------------------------------------------------------

    @mcp.tool
    async def ndp_register_url(
        resource_url: str,
        name: str,
        title: str = "",
        owner_org: str = "sage",
        file_type: str = "",
        notes: str = "",
        resource_name: str = "",
        confirm: bool = False,
    ) -> str:
        """Register an arbitrary URL as a dataset with one URL resource.

        Use this for data that already lives at a stable URL (an HTTP file, an
        S3 object, a stream). The URL is referenced, not copied. The owning
        organization must already exist — create it first with
        ndp_create_organization. Writes to the configured catalog (NDP_SERVER).
        Requires confirm=True.

        `name` is the dataset name. Set `resource_name` when the resource needs
        a different human-readable name; when omitted, the legacy URL endpoint
        is used and the resource name follows the dataset slug.

        file_type is an optional hint: stream, CSV, TXT, JSON, or NetCDF.
        """
        ndp = _ndp()
        resource_url = resource_url.strip()
        slug = reg_core.slugify(name)
        display_resource_name = resource_name.strip() or slug
        if not resource_url.startswith(("http://", "https://")):
            return (
                "URL registration failed: resource_url must be an absolute "
                "HTTP(S) URL beginning with http:// or https://."
            )
        if not confirm:
            return (
                f"This will register {resource_url} as resource "
                f"{display_resource_name!r} in dataset {slug!r} under org "
                f"{owner_org!r} in the {ndp.server} catalog. Call again with "
                "confirm=True to proceed."
            )

        try:
            if resource_name.strip():
                resource: Dict[str, Any] = {
                    "url": resource_url,
                    "name": display_resource_name,
                }
                if file_type:
                    resource["format"] = file_type
                payload: Dict[str, Any] = {
                    "name": slug,
                    "title": title or name,
                    "owner_org": owner_org,
                    "resources": [resource],
                    "private": False,
                }
                if notes:
                    payload["notes"] = notes
                result = await ndp.register_general_dataset(payload)
            else:
                payload = {
                    "resource_name": slug,
                    "resource_title": title or name,
                    "owner_org": owner_org,
                    "resource_url": resource_url,
                }
                if file_type:
                    payload["file_type"] = file_type
                if notes:
                    payload["notes"] = notes
                result = await ndp.register_url(payload)
        except NDPError as e:
            return f"URL registration failed: {e}"
        rid = result.get("id") if isinstance(result, dict) else result
        return (
            f"Registered URL resource {display_resource_name!r} in dataset "
            f"{slug!r} in the {ndp.server} catalog (id {rid}).\n"
            f"URL: {resource_url}"
        )

    # ------------------------------------------------------------------
    # Registration — local path
    # ------------------------------------------------------------------

    @mcp.tool
    async def ndp_register_local_path(
        path: str,
        title: str = "",
        description: str = "",
        owner_org: str = "",
        tags: str = "",
        license_id: str = "",
        private: bool = True,
        mode: Literal["auto", "single", "collection"] = "auto",
        dry_run: bool = True,
    ) -> str:
        """Register a local file or folder as an NDP dataset.

        A single file becomes a dataset with one resource. A folder becomes one
        dataset with one resource per file. Returns a PREVIEW by default —
        review it, then call ndp_finalize_registration to commit.

        Mixed file types in a folder will produce a question rather than a guess.
        """
        try:
            resources, questions, warnings = scan_path(path, mode=mode)
        except (FileNotFoundError, ValueError) as e:
            return f"Error scanning path: {e}"

        extras: Dict[str, Any] = {
            "source": "local_upload",
            "source_path": path,
            "storage_mode": "reference",
        }

        preview = reg_core.build_preview(
            source="local",
            title=title or None,
            notes=description or None,
            owner_org=owner_org or None,
            tags=[t.strip() for t in tags.split(",") if t.strip()] or None,
            license_id=license_id or None,
            private=private,
            resources=resources,
            extras=extras,
            questions=questions,
            warnings=warnings,
        )

        if dry_run or preview.status == "needs_input":
            return _render_preview(preview)
        return _render_result(await _commit(_ndp(), preview.staged_id))

    # ------------------------------------------------------------------
    # Registration — from Sage
    # ------------------------------------------------------------------

    @mcp.tool
    async def ndp_register_from_sage(
        time_range: str = "-1h",
        node_id: str = "",
        plugin: str = "",
        measurement: str = "",
        title: str = "",
        description: str = "",
        owner_org: str = "sage",
        tags: str = "",
        license_id: str = "",
        private: bool = True,
        max_records: int = 500,
        dry_run: bool = True,
    ) -> str:
        """Query Sage and register the result as an NDP dataset.

        Runs the Sage query itself, so full provenance (filter, time range,
        node VSNs, plugins) is captured automatically rather than reconstructed.

        Image/upload records are registered as Beehive URL references — the data
        is NOT copied into NDP. Non-upload measurements are summarized into
        dataset extras.

        Returns a PREVIEW by default. Call ndp_finalize_registration to commit.
        """
        filter_params: Dict[str, Any] = {}
        if plugin:
            filter_params["plugin"] = plugin if ".*" in plugin else f".*{plugin}.*"
        if node_id:
            # Accept both bare numbers ("029" -> "W029") and full VSNs of any
            # node family ("W029", "V031"). Only prepend "W" to a bare number;
            # never rewrite a VSN that already carries a letter prefix.
            vsn = node_id.strip().upper()
            filter_params["vsn"] = f"W{vsn}" if vsn.isdigit() else vsn
        if measurement:
            filter_params["name"] = measurement
        if not filter_params:
            return (
                "Specify at least one of: plugin, node_id, or measurement. "
                "An unfiltered query would return the entire Sage archive."
            )

        try:
            start, end = parse_time_range(time_range)
            df = data_service.query_data(
                start, end, dict(filter_params), max_records=max_records
            )
        except Exception as e:
            return f"Error querying Sage: {e}"

        if df is None or df.empty:
            return (
                f"Sage query returned no records for {filter_params} over {time_range}. "
                "Nothing to register. Try a wider time range or a different filter."
            )

        extras = sage_provenance(filter_params, start, end, df)
        resources = beehive_resources(df)

        warnings: List[str] = []
        if not resources:
            warnings.append(
                f"No Beehive file URLs in {len(df)} records — this query returned "
                "scalar measurements only. The dataset will carry summary metadata "
                "but no downloadable resources."
            )

        preview = reg_core.build_preview(
            source="sage",
            title=title or None,
            notes=description or None,
            owner_org=owner_org or None,
            tags=[t.strip() for t in tags.split(",") if t.strip()] or None,
            license_id=license_id or None,
            private=private,
            resources=resources,
            extras=extras,
            warnings=warnings,
        )

        if dry_run or preview.status == "needs_input":
            return _render_preview(preview)
        return _render_result(await _commit(_ndp(), preview.staged_id))

    @mcp.tool
    async def ndp_append_from_sage(
        dataset_name: str,
        time_range: str = "-1h",
        node_ids: str = "",
        plugin: str = "",
        measurement: str = "",
        max_records_per_node: int = 500,
        max_new_resources: int = 200,
        description_append: str = "",
        confirm: bool = False,
    ) -> str:
        """Append Beehive file resources from a Sage query to an NDP dataset.

        This is the additive counterpart to ``ndp_register_from_sage``. It
        resolves an existing dataset by exact name, queries one or more
        comma-separated Sage nodes, extracts authoritative Beehive URLs,
        removes URLs already cataloged, and PATCH-adds only new resources.

        The dataset stays in its current catalog and retains its existing
        resources. Query provenance is merged into dataset extras. Set
        ``description_append`` to append a human-readable update to the current
        dataset notes. Requires confirm=True to write.
        """
        if data_service is None:
            return "Sage append is unavailable: no Sage data service."
        if max_records_per_node < 1 or max_records_per_node > 5000:
            return "max_records_per_node must be between 1 and 5000."
        if max_new_resources < 1 or max_new_resources > 500:
            return "max_new_resources must be between 1 and 500."

        nodes = [n.strip().upper() for n in node_ids.split(",") if n.strip()]
        if not any((nodes, plugin.strip(), measurement.strip())):
            return (
                "Specify at least one of node_ids, plugin, or measurement. "
                "An unfiltered Sage archive query is not allowed."
            )

        ndp = _ndp()
        try:
            candidates = await ndp.search_datasets(
                [dataset_name],
                keys=["name"],
            )
        except NDPError as e:
            return f"Could not resolve dataset {dataset_name!r}: {e}"
        record = next(
            (
                item
                for item in candidates
                if isinstance(item, dict) and item.get("name") == dataset_name
            ),
            None,
        )
        if record is None:
            return f"Dataset {dataset_name!r} was not found in the {ndp.server} catalog."
        dataset_id = record.get("id")
        if not dataset_id:
            return f"Dataset {dataset_name!r} has no dataset ID; cannot patch it."

        existing_resources = record.get("resources") or []
        existing_urls = {
            str(resource.get("url") or resource.get("resource_url"))
            for resource in existing_resources
            if isinstance(resource, dict)
            and (resource.get("url") or resource.get("resource_url"))
        }

        start, end = parse_time_range(time_range)
        query_nodes: List[Optional[str]] = nodes or [None]
        new_resources: List[Any] = []
        seen_urls = set(existing_urls)
        query_filters: List[Dict[str, Any]] = []
        record_count = 0

        for node in query_nodes:
            filter_params: Dict[str, Any] = {}
            if plugin:
                filter_params["plugin"] = (
                    plugin if ".*" in plugin else f".*{plugin}.*"
                )
            if measurement:
                filter_params["name"] = measurement
            if node:
                filter_params["vsn"] = node
            query_filters.append(dict(filter_params))

            try:
                df = data_service.query_data(
                    start,
                    end,
                    dict(filter_params),
                    max_records=max_records_per_node,
                )
            except Exception as e:
                return f"Error querying Sage for {filter_params}: {e}"
            if df is None or df.empty:
                continue
            record_count += len(df)

            for resource in beehive_resources(df):
                if resource.url in seen_urls:
                    continue
                seen_urls.add(resource.url)
                new_resources.append(resource)
                if len(new_resources) >= max_new_resources:
                    break
            if len(new_resources) >= max_new_resources:
                break

        if not new_resources:
            return (
                f"No new Beehive resources found for {dataset_name!r}. "
                f"The query returned {record_count} record(s), and all file URLs "
                "were absent or already cataloged."
            )

        preview_lines = [
            f"Dataset: {dataset_name} (id {dataset_id})",
            f"Existing resources: {len(existing_resources)}",
            f"New unique Beehive resources: {len(new_resources)}",
            f"Resulting resources: {len(existing_resources) + len(new_resources)}",
            f"Sage records inspected: {record_count}",
            f"Time range: {start} to {end or 'now'}",
            "New resources:",
        ]
        preview_lines.extend(
            f"- {resource.name} [{resource.format or '?'}] {resource.url}"
            for resource in new_resources[:20]
        )
        if len(new_resources) > 20:
            preview_lines.append(f"... and {len(new_resources) - 20} more")
        preview = "\n".join(preview_lines)
        if not confirm:
            return (
                preview
                + "\n\nNothing written yet. Call again with the same arguments "
                "and confirm=True to append these resources."
            )

        extras = reg_core.stringify_extras(
            {
                "sage:last_append_query": query_filters,
                "sage:last_append_time_range": [start, end],
                "sage:last_append_nodes": nodes,
                "sage:last_append_record_count": record_count,
                "sage:last_append_resource_count": len(new_resources),
                "storage_mode": "reference",
                "storage_host": "storage.sagecontinuum.org",
                "source": "sage_continuum",
            }
        )
        payload: Dict[str, Any] = {
            "resources": [
                resource.model_dump(exclude_none=True)
                for resource in new_resources
            ],
            "extras": extras,
        }
        if description_append.strip():
            current_notes = str(record.get("notes") or "").strip()
            payload["notes"] = "\n\n".join(
                text
                for text in (current_notes, description_append.strip())
                if text
            )

        try:
            await ndp.patch_general_dataset(str(dataset_id), payload)
        except NDPError as e:
            return f"Could not append resources to {dataset_name!r}: {e}"

        return (
            preview
            + f"\n\nAppended {len(new_resources)} resource(s) to "
            f"{dataset_name!r} in the {ndp.server} catalog."
        )

    # ------------------------------------------------------------------
    # Resources — add to an existing dataset
    # ------------------------------------------------------------------

    @mcp.tool
    async def ndp_add_resource(
        dataset_name: str,
        resource_url: str,
        resource_name: str = "",
        file_type: str = "",
        description: str = "",
        confirm: bool = False,
    ) -> str:
        """Add a single URL resource to an existing NDP dataset, by name.

        Resolves the dataset by its exact name in the configured catalog
        (NDP_SERVER), then PATCH-adds the URL as a new resource. The URL is
        referenced, not copied, and existing resources are preserved (the patch
        merges). If the URL is already on the dataset, nothing is added.
        Requires confirm=True to write.

        Use this when the dataset already exists and you just want to attach one
        more file/stream by URL. To create a new dataset from a URL, use
        ndp_register_url instead. file_type is an optional hint: stream, CSV,
        TXT, JSON, or NetCDF.
        """
        ndp = _ndp()
        resource_url = resource_url.strip()
        if not resource_url.startswith(("http://", "https://")):
            return (
                "Add failed: resource_url must be an absolute HTTP(S) URL "
                "beginning with http:// or https://."
            )

        try:
            matches = await ndp.search_datasets([dataset_name], keys=["name"])
        except NDPError as e:
            return f"Could not look up {dataset_name!r}: {e}"
        record = next(
            (d for d in matches if isinstance(d, dict) and d.get("name") == dataset_name),
            None,
        )
        if record is None:
            return (
                f"No dataset named {dataset_name!r} in the {ndp.server} catalog. "
                "Create it first (e.g. ndp_register_url or ndp_register_from_sage)."
            )
        dataset_id = record.get("id")
        if not dataset_id:
            return f"Dataset {dataset_name!r} has no dataset ID; cannot patch it."

        existing = record.get("resources") or []
        existing_urls = {
            str(r.get("url") or r.get("resource_url"))
            for r in existing
            if isinstance(r, dict) and (r.get("url") or r.get("resource_url"))
        }
        if resource_url in existing_urls:
            return (
                f"{resource_url} is already a resource on {dataset_name!r}; "
                "nothing to add."
            )

        display_name = (
            resource_name.strip()
            or resource_url.rstrip("/").split("/")[-1]
            or "resource"
        )
        if not confirm:
            return (
                f"This will add resource {display_name!r} ({resource_url}) to "
                f"dataset {dataset_name!r} in the {ndp.server} catalog, keeping its "
                f"{len(existing)} existing resource(s). Call again with "
                "confirm=True to proceed."
            )

        resource: Dict[str, Any] = {"url": resource_url, "name": display_name}
        if file_type:
            resource["format"] = file_type
        if description:
            resource["description"] = description

        try:
            await ndp.patch_general_dataset(str(dataset_id), {"resources": [resource]})
        except NDPError as e:
            return f"Could not add resource to {dataset_name!r}: {e}"
        return (
            f"Added resource {display_name!r} to {dataset_name!r} in the "
            f"{ndp.server} catalog (now {len(existing) + 1} resources).\n"
            f"URL: {resource_url}"
        )

    # ------------------------------------------------------------------
    # Finalize
    # ------------------------------------------------------------------

    @mcp.tool
    async def ndp_finalize_registration(
        staged_id: str,
        title: str = "",
        description: str = "",
        owner_org: str = "",
        tags: str = "",
        private: Optional[bool] = None,
        confirm: bool = False,
    ) -> str:
        """Apply edits/answers to a staged registration and commit it to NDP.

        Call with confirm=True to actually write. Without it, returns the
        updated preview so you can check the edits took effect.

        Any field left blank keeps its staged value.
        """
        reg = staging.get(staged_id)
        if reg is None:
            return f"No staged registration {staged_id!r} (it may have expired). Re-run the register tool."

        edits: Dict[str, Any] = {}
        if title:
            edits["title"] = title
            edits["name"] = reg_core.unique_slug(title, salt=str(len(reg.resources)))
        if description:
            edits["notes"] = description
        if owner_org:
            edits["owner_org"] = owner_org
        if tags:
            edits["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        if private is not None:
            edits["private"] = private

        if edits:
            edits["questions"] = []  # answering by editing resolves the questions
            staging.update(staged_id, **edits)

        reg = staging.get(staged_id)
        if reg.questions:
            return _render_preview(reg_core.preview_from_staged(staged_id, reg))

        if not confirm:
            preview = reg_core.preview_from_staged(staged_id, reg)
            return _render_preview(preview) + "\n\nCall again with confirm=True to commit."

        return _render_result(await _commit(_ndp(), staged_id))

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    @mcp.tool
    async def ndp_publish_dataset(
        dataset_name: str,
        target: Literal["", "local", "pre_ckan"] = "",
        confirm: bool = False,
    ) -> str:
        """Publish a registered dataset to a chosen NDP catalog.

        `target` selects the destination catalog:
          - "local"    → this endpoint's own catalog (stays off the public platform)
          - "pre_ckan" → the public National Data Platform catalog
          - ""         → defaults to the configured NDP_SERVER (usually 'local')

        Looks the dataset up by name and re-registers it against `target`.
        A separate, deliberate step — requires confirm=True. Publishing to
        pre_ckan makes the dataset externally visible.
        """
        ndp = _ndp()
        dest = (target or ndp.server or "local").strip()
        if dest not in ("local", "pre_ckan"):
            return f"Invalid target {dest!r}; use 'local' or 'pre_ckan'."

        try:
            matches = await ndp.search_datasets([dataset_name], keys=["name"])
        except NDPError as e:
            return f"Could not look up {dataset_name!r}: {e}"

        record = next((d for d in matches if d.get("name") == dataset_name), None)
        if record is None:
            return (
                f"No dataset named {dataset_name!r} found in the {ndp.server} catalog. "
                "Register it first, then publish."
            )

        n_res = len(record.get("resources") or [])
        catalog = (
            "this endpoint's local catalog"
            if dest == "local"
            else "the public NDP catalog (PRE-CKAN)"
        )
        if not confirm:
            visibility = (
                "It stays private to your endpoint."
                if dest == "local"
                else "This makes it externally visible."
            )
            return (
                f"This will publish {dataset_name!r} ({record.get('title')}, "
                f"{n_res} resources) to {catalog}. {visibility} "
                "Call again with confirm=True to proceed."
            )

        payload = {
            "name": record.get("name"),
            "title": record.get("title"),
            "owner_org": record.get("owner_org") or record.get("organization", {}).get("name"),
            "notes": record.get("notes"),
            "tags": [
                t.get("name") if isinstance(t, dict) else t
                for t in (record.get("tags") or [])
            ],
            "extras": record.get("extras"),
            "resources": [
                {
                    "url": r.get("url"),
                    "name": r.get("name"),
                    "format": r.get("format"),
                    "description": r.get("description"),
                }
                for r in (record.get("resources") or [])
                if r.get("url") and r.get("name")
            ],
            "private": dest != "pre_ckan",
            "license_id": record.get("license_id"),
        }
        payload = {k: v for k, v in payload.items() if v not in (None, [], {})}

        try:
            result = await ndp.publish_to_server(payload, dest)
        except NDPError as e:
            return f"Publish failed: {e}"
        return f"Published {dataset_name!r} to {dest} ({catalog}), {n_res} resources.\n{result}"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _commit(ndp: NDPClient, staged_id: str) -> RegistrationResult:
    reg = staging.get(staged_id)
    if reg is None:
        return RegistrationResult(status="failed", errors=[f"staged id {staged_id} expired"])

    warnings = list(reg.warnings)

    # Idempotent organization handling, per the NDP reference notebook.
    try:
        if await ndp.ensure_organization(reg.owner_org):
            warnings.append(f"Created organization {reg.owner_org!r}.")
    except NDPError as e:
        return RegistrationResult(
            status="failed", dataset_name=reg.name, errors=[f"organization check: {e}"]
        )

    # Dataset names are globally unique; re-slug on collision rather than fail.
    try:
        if await ndp.dataset_exists(reg.name):
            original = reg.name
            reg.name = reg_core.unique_slug(reg.title, salt=str(time.time()))
            warnings.append(f"{original!r} already existed; registered as {reg.name!r}.")
    except NDPError:
        pass  # search is best-effort; let the create call be authoritative

    payload = reg_core.to_request(reg).model_dump(exclude_none=True)
    try:
        response = await ndp.register_general_dataset(payload)
    except NDPError as e:
        return RegistrationResult(
            status="failed",
            dataset_name=reg.name,
            errors=[str(e)],
        )

    dataset_id = None
    if isinstance(response, dict):
        dataset_id = response.get("id") or response.get("dataset_id")

    staging.drop(staged_id)
    return RegistrationResult(
        status="registered",
        dataset_id=dataset_id,
        dataset_name=reg.name,
        resources_created=len(reg.resources),
        server=ndp.server,
        warnings=warnings,
    )


def _render_preview(p: RegistrationPreview) -> str:
    lines = ["=== NDP registration preview (nothing written yet) ===", p.summary(), ""]
    if p.resources:
        lines.append(f"Resources ({p.resource_count}, showing {len(p.resources)}):")
        for r in p.resources[:10]:
            lines.append(f"  - {r.name} [{r.format or '?'}] {r.url}")
        if p.resource_count > 10:
            lines.append(f"  ... and {p.resource_count - 10} more")
        lines.append("")
    if p.extras:
        lines.append("Extras:")
        for k, v in list(p.extras.items())[:15]:
            lines.append(f"  {k}: {v}")
        lines.append("")
    if p.assumptions:
        lines.append("Assumptions: " + "; ".join(p.assumptions))
    lines.append(f"staged_id: {p.staged_id}")
    if p.status == "needs_input":
        lines.append("Answer the question(s) above via ndp_finalize_registration.")
    else:
        lines.append("Commit with: ndp_finalize_registration(staged_id, confirm=True)")
    return "\n".join(lines)


def _render_result(r: RegistrationResult) -> str:
    if r.status == "failed":
        return f"Registration FAILED for {r.dataset_name}: " + "; ".join(r.errors)
    target = r.server or "local"
    catalog = "this endpoint's local catalog" if target == "local" else "the public NDP catalog (PRE-CKAN)"
    lines = [
        f"Registered dataset {r.dataset_name!r}",
        f"  id:        {r.dataset_id}",
        f"  resources: {r.resources_created}",
        f"  catalog:   {target} ({catalog})",
    ]
    for w in r.warnings:
        lines.append(f"  ! {w}")
    lines.append("")
    if target == "local":
        lines.append(
            "This stayed private to your endpoint. To make it public on the National "
            f"Data Platform, run: ndp_publish_dataset({r.dataset_name!r}, confirm=True)"
        )
    else:
        lines.append("This was written directly to the public PRE-CKAN catalog.")
    return "\n".join(lines)
