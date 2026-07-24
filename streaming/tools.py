"""Streaming MCP tools.

Registered onto the existing FastMCP instance alongside the Sage and NDP tools.
Purely additive — nothing in the Sage- or NDP-side modules is modified.

These tools drive the derived-stream lifecycle for the SAGE api_stream:

    ensure  -> profile -> create -> list -> sample -> delete

A derived stream is a private Kafka topic fed by a background producer that
reads the live SAGE feed, applies the agent's filters, and forwards only the
matching rows. The topic is registered at NDP as a `kafka` resource carrying
its source lineage + filters, so it is discoverable and consumable.

Session-scoped lifetime: the producer runs inside THIS server process. A topic
keeps loading only while this server stays up. `stream_delete` frees the Kafka
topic + NDP resource.
"""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional

from .client import (
    SAGE_DATASET,
    SAGE_RESOURCE,
    StreamingError,
    StreamingRuntime,
)

logger = logging.getLogger(__name__)


def _parse_filters(text: str) -> List[Any]:
    """Parse the `filters` argument into a list of rules.

    Accepts either a JSON array (of strings and/or rule dicts) or newline-
    separated string expressions. Newlines are used as the separator because a
    single expression may itself contain commas (e.g. ``vsn in [W06C, W029]``).
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
    except (json.JSONDecodeError, ValueError):
        pass
    return [line.strip() for line in text.splitlines() if line.strip()]


def _fmt_profile(profile: dict) -> List[str]:
    lines: List[str] = []
    for field, stats in profile.items():
        bits = [f"non_null={stats.get('non_null', 0)}", f"distinct={stats.get('distinct_count', 0)}"]
        if "numeric_min" in stats:
            bits.append(f"range=[{stats['numeric_min']:g}..{stats['numeric_max']:g}]")
        lines.append(f"  {field}: {', '.join(bits)}")
        if "values" in stats:
            preview = ", ".join(str(v) for v in stats["values"])
            lines.append(f"      values: {preview}")
        elif "sample_values" in stats:
            preview = ", ".join(str(v) for v in stats["sample_values"])
            lines.append(f"      e.g.: {preview} ...")
    return lines


def register(mcp, ndp_client: Optional[Any] = None) -> None:
    """Attach the `stream_*` tools to `mcp`.

    `ndp_client` is an optional pre-built ``ndp.NDPClient`` reused for the
    org/dataset ensure step; when omitted the runtime builds its own lazily.
    """
    _runtime_holder: dict[str, Any] = {"runtime": None}

    def _rt() -> StreamingRuntime:
        if _runtime_holder["runtime"] is None:
            _runtime_holder["runtime"] = StreamingRuntime(ndp_client=ndp_client)
        return _runtime_holder["runtime"]

    # ------------------------------------------------------------------
    # 1. Ensure the SAGE source is registered
    # ------------------------------------------------------------------
    @mcp.tool
    async def stream_ensure_sage_source(confirm: bool = False) -> str:
        """Ensure the SAGE live-stream source is registered in the NDP catalog.

        Idempotently creates the 'sage' organization and 'sage' dataset if
        missing, then upserts the 'SAGE Data' api_stream resource (the live SSE
        feed of all SAGE nodes). Safe to call repeatedly — the resource is
        upserted by name. This is the bootstrap every other stream_* tool
        depends on. Writes to the configured catalog (NDP_SERVER); requires
        confirm=True.
        """
        rt = _rt()
        if not confirm:
            return (
                "This will ensure org 'sage', dataset 'sage', and the 'SAGE Data' "
                f"api_stream resource exist in the {rt.server} catalog (idempotent). "
                "Call again with confirm=True to proceed."
            )
        try:
            handle = await rt.ensure_sage_source()
        except StreamingError as exc:
            return f"Ensure failed: {exc}"
        created = ", ".join(handle["created"]) if handle["created"] else "nothing new"
        return (
            f"SAGE source ready in the {handle['server']} catalog (created: {created}).\n"
            f"  dataset:     {handle['dataset']}\n"
            f"  resource:    {handle['resource']}\n"
            f"  resource_id: {handle['resource_id']}\n"
            "Next: stream_profile_sage to see which fields/values you can filter on."
        )

    # ------------------------------------------------------------------
    # 2. Profile the source (read-only)
    # ------------------------------------------------------------------
    @mcp.tool
    async def stream_profile_sage(max_records: int = 40, timeout_seconds: int = 15) -> str:
        """Sample the live SAGE feed and report its fields and observed values.

        Read-only: briefly connects to the SAGE SSE stream, collects up to
        `max_records` events, and summarizes each field — distinct values for
        low-cardinality fields (like `vsn` and `name`) and numeric ranges for
        measurements. Use this to choose real filter values before creating a
        derived stream, instead of guessing. The two most useful fields are
        usually `vsn` (which node) and `name` (which measurement).
        """
        if max_records < 1 or max_records > 500:
            return "max_records must be between 1 and 500."
        if timeout_seconds < 2 or timeout_seconds > 60:
            return "timeout_seconds must be between 2 and 60."
        try:
            result = await _rt().profile_sage(
                max_records=max_records, timeout_seconds=float(timeout_seconds)
            )
        except StreamingError as exc:
            return f"Profiling failed: {exc}"
        if result["sampled"] == 0:
            return (
                "No events were received from the SAGE feed within the time limit. "
                "Try a larger timeout_seconds. (The source may be briefly quiet.)"
            )
        lines = [
            f"Profiled {result['sampled']} live SAGE event(s) from {result['url']}",
            f"Schema (filterable fields): {', '.join(result['schema'])}",
            "",
            "Field profile:",
        ]
        lines.extend(_fmt_profile(result["profile"]))
        lines.append("")
        lines.append(
            "Build filters as expressions, one per line, e.g.:\n"
            "  vsn == W06C\n  name == env.temperature\n"
            "Then call stream_create_derived(filters=...)."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 3. Create a derived stream
    # ------------------------------------------------------------------
    @mcp.tool
    async def stream_create_derived(
        filters: str,
        description: str = "",
        dataset: str = SAGE_DATASET,
        resource: str = SAGE_RESOURCE,
        confirm: bool = False,
    ) -> str:
        """Create a filtered, derived Kafka stream from the SAGE source.

        Compiles `filters`, creates a new private Kafka topic, registers it at
        NDP as a `kafka` resource (carrying source lineage + the filters as
        metadata), and starts a background producer that forwards only matching
        SAGE rows into the topic. Non-blocking: returns once the topic exists.

        `filters`: expressions, ONE PER LINE (a single line may contain commas),
        e.g.:
            vsn == W06C
            name == upload
        A JSON array of expressions/rule-dicts is also accepted. An empty
        `filters` forwards the full SAGE feed (discouraged — high volume).

        `description` is stored on the derived resource — write what the stream
        is for; the topic name itself is opaque. Requires confirm=True. Free the
        stream later with stream_delete.
        """
        exprs = _parse_filters(filters)
        preview_filters = "\n".join(f"    {e}" for e in exprs) if exprs else "    (none — full feed)"
        if not confirm:
            warn = ""
            if not exprs:
                warn = (
                    "\nWARNING: no filters — this forwards the ENTIRE SAGE feed into a "
                    "Kafka topic. Add at least one filter unless you really want everything."
                )
            return (
                f"This will create a derived Kafka stream from [{dataset} / {resource}] "
                f"with filters:\n{preview_filters}\n"
                f"description: {description or '(none)'}{warn}\n"
                "Call again with confirm=True to create it."
            )
        try:
            result = await _rt().create_derived(
                filter_exprs=exprs,
                description=description or None,
                dataset=dataset,
                resource=resource,
            )
        except StreamingError as exc:
            return f"Create failed: {exc}"
        lines = [
            f"Derived stream created (topic newly created: {result['created_topic']}).",
            f"  topic:       {result['topic']}",
            f"  resource_id: {result['resource_id']}",
            f"  dataset_id:  {result['dataset_id']}",
            f"  filters:     {json.dumps(result['filters'])}",
            "",
            "The background producer is now forwarding matching SAGE rows into this topic.",
            "It stays live only while this MCP server process is up.",
            "Verify with: stream_sample(topic). Free it with: stream_delete(topic).",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 4. List my derived streams
    # ------------------------------------------------------------------
    @mcp.tool
    async def stream_list() -> str:
        """List the derived streams you own (by Kafka topic prefix).

        Shows each topic, its NDP resource/dataset ids, and whether a live
        producer for it is running in THIS server process.
        """
        try:
            rows = await _rt().list_streams()
        except StreamingError as exc:
            return f"Could not list streams: {exc}"
        if not rows:
            return "You have no derived streams. Create one with stream_create_derived."
        out = [f"You own {len(rows)} derived stream(s):"]
        for row in rows:
            resources = ", ".join(row.get("resources") or []) or "-"
            datasets = ", ".join(row.get("datasets") or []) or "-"
            status = "inactive" if row.get("inactive") else "active"
            if row.get("local_producer"):
                status += ",local-producer"
            out.append(
                f"  [{row.get('suffix')}] topic={row.get('topic')} "
                f"resources={resources} datasets={datasets} status={status}"
            )
        return "\n".join(out)

    # ------------------------------------------------------------------
    # 5. Sample a derived topic (prove data is flowing)
    # ------------------------------------------------------------------
    @mcp.tool
    async def stream_sample(
        topic: str,
        timeout_seconds: int = 8,
        from_beginning: bool = True,
        limit: int = 20,
    ) -> str:
        """Consume a derived topic briefly and return a snapshot of its records.

        Attaches a temporary consumer, buffers for `timeout_seconds`, then
        returns up to `limit` records plus a summary. Use this to confirm a
        derived stream is actually receiving filtered data.
        """
        if timeout_seconds < 1 or timeout_seconds > 60:
            return "timeout_seconds must be between 1 and 60."
        if limit < 1 or limit > 200:
            return "limit must be between 1 and 200."
        try:
            result = await _rt().sample_topic(
                topic,
                timeout_seconds=float(timeout_seconds),
                from_beginning=from_beginning,
                limit=limit,
            )
        except StreamingError as exc:
            return f"Sample failed: {exc}"
        summary = result["summary"]
        records = result["records"]
        lines = [
            f"Topic: {topic}",
            f"  records consumed this session: {summary.get('total_consumed', 0)}",
            f"  buffered now: {summary.get('stored_records', 0)}",
            f"  columns: {', '.join(summary.get('columns') or []) or '-'}",
        ]
        if not records:
            lines.append(
                "\nNo records in the window. Either the filters match nothing right now, "
                "or the producer isn't running (was the server restarted since create?)."
            )
            return "\n".join(lines)
        lines.append(f"\nFirst {min(limit, len(records))} record(s):")
        for record in records[:limit]:
            lines.append(f"  {json.dumps(record, default=str)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 5b. Live tail — rolling realtime view of the filtered stream
    # ------------------------------------------------------------------
    @mcp.tool
    async def stream_tail(
        topic: str,
        limit: int = 20,
        warmup_seconds: int = 3,
        where: str = "",
        stop: bool = False,
    ) -> str:
        """Show NEW records arriving on a derived topic since your last call.

        A derived topic already contains only the rows that passed its filter,
        so this is a live view of the *filtered* stream. The first call starts a
        persistent consumer (reading only new data) and waits `warmup_seconds`;
        each later call returns just what arrived since. Call it repeatedly — or
        drive it with `/loop <interval> stream_tail <topic>` — to get a rolling,
        near-realtime feed in chat. (MCP tools are request/response, so this
        polls; it cannot auto-push. For a truly continuous visual, use the Kafka
        UI.)

        `where`: optional extra filter expressions (one per line) to narrow the
        view further, client-side — e.g. `value > 25`.
        `stop=True`: stop watching this topic and free the consumer.
        """
        if stop:
            try:
                result = await _rt().tail(topic, stop=True)
            except StreamingError as exc:
                return f"Stop failed: {exc}"
            return (
                f"Stopped tailing {topic}." if result.get("stopped")
                else f"Was not tailing {topic}; nothing to stop."
            )
        if limit < 1 or limit > 200:
            return "limit must be between 1 and 200."
        if warmup_seconds < 1 or warmup_seconds > 30:
            return "warmup_seconds must be between 1 and 30."
        try:
            result = await _rt().tail(
                topic,
                limit=limit,
                warmup_seconds=float(warmup_seconds),
                where=_parse_filters(where) if where.strip() else None,
            )
        except StreamingError as exc:
            return f"Tail failed: {exc}"

        summary = result["summary"]
        records = result["records"]
        header = (
            f"Now tailing {topic} (started this call). "
            if result["started_now"] else f"Tailing {topic}. "
        )
        header += f"total consumed this session: {summary.get('total_consumed', 0)}"
        if not records:
            hint = (
                "\nNo new records since last call. Call again in a few seconds "
                "(or use /loop). If it stays empty, the producer may be stopped "
                "or the filter matches nothing right now."
            )
            return header + hint
        lines = [header, f"\n{len(records)} new record(s):"]
        for record in records:
            lines.append(f"  {json.dumps(record, default=str)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 6. Delete / free
    # ------------------------------------------------------------------
    @mcp.tool
    async def stream_delete(
        target: str = "all",
        delete_topic: bool = True,
        confirm: bool = False,
    ) -> str:
        """Delete derived stream(s): stop the producer, remove the NDP resource and Kafka topic.

        `target` is "all", a full topic name, or a numeric suffix (e.g. "0").
        Set `delete_topic=False` to keep the Kafka topic and only remove the NDP
        resource(s). This is what frees Kafka broker resources. Requires
        confirm=True.
        """
        rt = _rt()
        if not confirm:
            scope = "ALL your derived streams" if target == "all" else f"stream target '{target}'"
            keep = " (Kafka topic kept)" if not delete_topic else ""
            return (
                f"This will stop and delete {scope}{keep}: it stops the fan-in producer, "
                f"deletes the NDP resource(s), and deletes the Kafka topic. "
                "Call again with confirm=True to proceed."
            )
        try:
            results = await rt.delete_streams(target, delete_topic=delete_topic)
        except StreamingError as exc:
            return f"Delete failed: {exc}"
        if not results:
            return f"No matching derived streams found for target '{target}'."
        lines = ["Deletion complete:"]
        for entry in results if isinstance(results, list) else []:
            lines.append(
                f"  topic={entry.get('topic')} "
                f"deleted_resources={entry.get('deleted_resources')} "
                f"deleted_topic={entry.get('deleted_topic')}"
            )
        return "\n".join(lines) if len(lines) > 1 else "Deletion completed."
