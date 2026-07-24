"""Streaming runtime — thin wrapper over `scidx_streaming_v2.StreamingClient`.

All derived-stream traffic goes through here. `StreamingClient` is synchronous
and its stream ops touch the network + Kafka, so calls are offloaded to threads
to avoid blocking the MCP event loop.

Config (env), shared with the NDP layer:
    NDP_API_URL    base URL of the NDP endpoint API
    NDP_API_KEY    bearer token (NDP_API_TOKEN accepted as a fallback)
    NDP_SERVER     which catalog to target: 'local' (default) or 'pre_ckan'

Lifetime model (important):
    A derived stream is only *loaded* while its background producer runs, and
    that producer lives inside THIS server process (an in-process daemon
    thread). The Kafka topic and NDP resource survive a restart, but the
    SAGE->Kafka fan-in stops. So a topic is "live" only while this server is up
    and holds its producer. There is no server-side durable runner yet.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ._kafka_compat import apply_patches as _apply_kafka_patches

logger = logging.getLogger(__name__)

# Neutralize the kafka-python 3.0.9 `endpoint_type` bug before any admin client
# is constructed; otherwise StreamingClient init silently drops its Kafka
# connection and every create_stream reports "Kafka connection is unavailable".
_apply_kafka_patches()

# --- SAGE source: server-owned plumbing, never the agent's to author --------
# The URL and field mapping are SAGE-specific wiring. If a model hallucinated
# them the stream would silently produce nothing, so they are baked in here and
# the agent only ever supplies filters + descriptions.
SAGE_ORG = "sage"
SAGE_DATASET = "sage"
SAGE_RESOURCE = "SAGE Data"
SAGE_SOURCE_TEMPLATE: Dict[str, Any] = {
    "type": "api_stream",
    "name": SAGE_RESOURCE,
    "description": "Live data from all SAGE nodes",
    "url": "https://data.sagecontinuum.org/api/v0/stream",
    "batch_mode": False,
    "mapping": {
        "timestamp": "timestamp",
        "name": "name",
        "value": "value",
        "vsn": "meta.vsn",
        "sensor": "meta.sensor",
        "class": "meta.class",
        "score": "meta.score",
        "plugin": "meta.plugin",
        "task": "meta.task",
        "job": "meta.job",
    },
}


class StreamingError(Exception):
    """Raised when a streaming operation fails."""


def _resolve_token() -> Optional[str]:
    return os.getenv("NDP_API_KEY") or os.getenv("NDP_API_TOKEN")


class StreamingRuntime:
    """Owns the process-wide StreamingClient and drives the derived-stream lifecycle.

    A single instance is held by the tool layer for the life of the server so
    that the ``_derived_producers`` registry (and therefore ``list``/``delete``)
    sees producers created by earlier tool calls.
    """

    def __init__(self, ndp_client: Any | None = None) -> None:
        self.base_url = os.getenv("NDP_API_URL")
        self.token = _resolve_token()
        self.server = (os.getenv("NDP_SERVER", "local") or "local").strip()
        self._streaming_client: Any = None
        self._ndp_client = ndp_client  # optional ndp.NDPClient for org/dataset ensure
        # Persistent consumers for live tailing, keyed by topic. Each keeps a
        # StreamHandle running so successive tail() calls return only new rows.
        self._consumers: Dict[str, Any] = {}
        self._cursors: Dict[str, int] = {}

    # -- lazy clients -------------------------------------------------------
    def _streaming(self) -> Any:
        """Build (once) and return the underlying StreamingClient."""
        if self._streaming_client is not None:
            return self._streaming_client
        if not self.base_url:
            raise StreamingError("NDP_API_URL is not set (env).")
        if not self.token:
            raise StreamingError("No NDP token found — set NDP_API_KEY (or NDP_API_TOKEN).")
        try:
            from ndp_ep import APIClient
            from scidx_streaming_v2 import StreamingClient
        except Exception as exc:  # pragma: no cover - import/env dependent
            raise StreamingError(
                "scidx_streaming_v2 is not importable. Install it with "
                "`pip install -e streaming_v2` (see init_setup.sh). Cause: %s" % exc
            ) from exc
        try:
            ep = APIClient(base_url=self.base_url, token=self.token)
            self._streaming_client = StreamingClient(ep)
        except Exception as exc:
            raise StreamingError(f"Could not initialize StreamingClient: {exc}") from exc
        logger.info(
            "StreamingClient ready (endpoint=%s, kafka=%s:%s, user=%s)",
            self.base_url,
            getattr(self._streaming_client, "kafka_host", None),
            getattr(self._streaming_client, "kafka_port", None),
            getattr(self._streaming_client, "user_id", None),
        )
        return self._streaming_client

    def _ndp(self) -> Any:
        """Return an ndp.NDPClient for org/dataset ensure (handles draft->active)."""
        if self._ndp_client is None:
            try:
                from ndp.client import NDPClient
            except Exception as exc:  # pragma: no cover - layout dependent
                raise StreamingError(f"NDP client unavailable for catalog ensure: {exc}") from exc
            self._ndp_client = NDPClient()
        return self._ndp_client

    def kafka_status(self) -> Dict[str, Any]:
        """Report resolved Kafka endpoint + whether an admin connection exists."""
        client = self._streaming()
        return {
            "kafka_host": getattr(client, "kafka_host", None),
            "kafka_port": getattr(client, "kafka_port", None),
            "bootstrap": getattr(client, "kafka_bootstrap", None),
            "connected": getattr(client, "kafka_connection", None) is not None,
            "user_id": getattr(client, "user_id", None),
        }

    # -- 1. ensure the SAGE source -----------------------------------------
    async def ensure_sage_source(self) -> Dict[str, Any]:
        """Idempotently register org 'sage', dataset 'sage', and the api_stream resource.

        The resource is upserted by name, so re-running is safe. Returns the
        handle downstream tools use: ``{dataset, resource, resource_id}``.
        """
        ndp = self._ndp()
        created: List[str] = []
        try:
            if await ndp.ensure_organization(SAGE_ORG, "SAGE", "Datasets from the SAGE platform"):
                created.append("organization")
            if not await ndp.dataset_exists(SAGE_DATASET):
                await ndp.register_general_dataset(
                    {
                        "name": SAGE_DATASET,
                        "title": "SAGE",
                        "notes": "Data from SAGE Cloud",
                        "owner_org": SAGE_ORG,
                    }
                )
                created.append("dataset")
        except Exception as exc:
            raise StreamingError(f"Catalog ensure failed: {exc}") from exc

        def _register() -> Mapping[str, Any]:
            return self._streaming().register_resource(
                SAGE_DATASET, dict(SAGE_SOURCE_TEMPLATE), server=self.server
            )

        try:
            entry = await asyncio.to_thread(_register)
        except Exception as exc:
            raise StreamingError(f"Could not register SAGE api_stream resource: {exc}") from exc
        return {
            "dataset": SAGE_DATASET,
            "resource": SAGE_RESOURCE,
            "resource_id": entry.get("id") if isinstance(entry, Mapping) else None,
            "created": created,
            "server": self.server,
        }

    # -- 2. profile the SAGE source (the piece that unlocks AI filtering) ---
    async def profile_sage(self, *, max_records: int = 40, timeout_seconds: float = 15.0) -> Dict[str, Any]:
        """Briefly read the live SAGE SSE feed and summarize its fields + values.

        Reuses the real api_stream SSE handler so the profiled schema is exactly
        what filters operate on (post-mapping: vsn, name, value, ...).
        """
        records = await _sample_api_stream(
            dict(SAGE_SOURCE_TEMPLATE), max_records=max_records, timeout=timeout_seconds
        )
        return {
            "schema": sorted(SAGE_SOURCE_TEMPLATE["mapping"].keys()),
            "sampled": len(records),
            "profile": _summarize_records(records),
            "url": SAGE_SOURCE_TEMPLATE["url"],
        }

    # -- 3. create a derived stream ----------------------------------------
    async def create_derived(
        self,
        *,
        filter_exprs: Sequence[Any],
        description: str | None,
        dataset: str = SAGE_DATASET,
        resource: str = SAGE_RESOURCE,
    ) -> Dict[str, Any]:
        """Compile filters, create the derived Kafka topic, register it at NDP, start fan-in."""

        def _create() -> Any:
            client = self._streaming()
            compiled = client.compile_filters(list(filter_exprs)) if filter_exprs else None
            return client.create_stream(
                resources=[[dataset, resource]],
                filters=compiled,
                description=description,
                server=self.server,
            )

        try:
            result = await asyncio.to_thread(_create)
        except Exception as exc:
            raise StreamingError(f"Derived stream creation failed: {exc}") from exc
        return {
            "topic": getattr(result, "topic", None),
            "resource_id": getattr(result, "resource_id", None),
            "dataset_id": getattr(result, "dataset_id", None),
            "created_topic": bool(getattr(result, "created_topic", False)),
            "filters": [_jsonable(f) for f in (getattr(result, "filters", ()) or ())],
        }

    # -- 4. list my derived streams ----------------------------------------
    async def list_streams(self) -> List[Dict[str, Any]]:
        def _list() -> List[Dict[str, Any]]:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rows = self._streaming().view_my_streams(server=self.server, include_details=True)
            return rows or []

        try:
            return await asyncio.to_thread(_list)
        except Exception as exc:
            raise StreamingError(f"Could not list streams: {exc}") from exc

    # -- 5. sample a derived topic (prove data is flowing) -----------------
    async def sample_topic(
        self,
        topic: str,
        *,
        timeout_seconds: float = 8.0,
        from_beginning: bool = True,
        limit: int = 20,
    ) -> Dict[str, Any]:
        def _start() -> Any:
            client = self._streaming()
            handle = client.consume_stream(topic, from_beginning=from_beginning)
            handle.start(from_beginning=from_beginning)
            return handle

        try:
            handle = await asyncio.to_thread(_start)
        except Exception as exc:
            raise StreamingError(f"Could not start consumer for topic '{topic}': {exc}") from exc

        await asyncio.sleep(max(0.5, timeout_seconds))

        def _snapshot() -> Dict[str, Any]:
            try:
                records = [dict(r) for r in handle.records(limit=limit)]
                summary = dict(handle.summary())
            finally:
                handle.stop()
            return {"records": records, "summary": summary}

        try:
            return await asyncio.to_thread(_snapshot)
        except Exception as exc:
            raise StreamingError(f"Could not sample topic '{topic}': {exc}") from exc

    # -- 5b. live tail: new records since the last call --------------------
    async def tail(
        self,
        topic: str,
        *,
        limit: int = 20,
        warmup_seconds: float = 3.0,
        where: Sequence[Any] | None = None,
        stop: bool = False,
    ) -> Dict[str, Any]:
        """Return records that arrived on `topic` since the previous tail() call.

        The first call starts a persistent consumer (reading only *new* data,
        i.e. from the latest offset) and waits `warmup_seconds` to collect an
        initial batch. Subsequent calls return just the delta. Repeated calls
        give a rolling, near-realtime view of the (already filtered) stream.

        `where` optionally narrows the view further, client-side.
        `stop=True` stops watching and frees the consumer.
        """
        if stop:
            stopped = await asyncio.to_thread(self._stop_consumer, topic)
            return {"stopped": stopped, "records": [], "summary": {}, "started_now": False}

        started_now = False
        if topic not in self._consumers:
            def _start() -> Any:
                handle = self._streaming().consume_stream(topic, from_beginning=False)
                handle.start(from_beginning=False)
                return handle

            try:
                self._consumers[topic] = await asyncio.to_thread(_start)
            except Exception as exc:
                raise StreamingError(f"Could not start tail consumer for '{topic}': {exc}") from exc
            self._cursors[topic] = 0
            started_now = True
            await asyncio.sleep(max(0.5, warmup_seconds))

        handle = self._consumers[topic]

        def _pull() -> tuple[list[dict[str, Any]], dict[str, Any]]:
            summary = dict(handle.summary())
            total = int(summary.get("total_consumed", 0))
            prev = self._cursors.get(topic, 0)
            new_count = max(0, total - prev)
            records = [dict(r) for r in handle.records(limit=new_count)] if new_count else []
            self._cursors[topic] = total
            return records, summary

        try:
            records, summary = await asyncio.to_thread(_pull)
        except Exception as exc:
            raise StreamingError(f"Could not read tail for '{topic}': {exc}") from exc

        if where:
            records = self._apply_view_filter(records, where)
        return {"records": records[-limit:], "summary": summary, "started_now": started_now}

    def _stop_consumer(self, topic: str) -> bool:
        """Stop and forget a persistent tail consumer for a topic."""
        handle = self._consumers.pop(topic, None)
        self._cursors.pop(topic, None)
        if handle is None:
            return False
        try:
            handle.stop()
        except Exception:
            logger.debug("Failed to stop tail consumer for %s", topic, exc_info=True)
        return True

    def _apply_view_filter(
        self, records: Sequence[Mapping[str, Any]], where: Sequence[Any]
    ) -> List[Dict[str, Any]]:
        """Apply extra client-side filter expressions to already-consumed records."""
        if not records:
            return []
        try:
            from scidx_streaming_v2.filters import apply_filters
            compiled = self._streaming().compile_filters(list(where))
            frame = apply_filters([dict(r) for r in records], compiled)
        except Exception as exc:
            logger.warning("View filter failed (%s); returning unfiltered rows.", exc)
            return [dict(r) for r in records]
        if getattr(frame, "empty", True):
            return []
        return frame.to_dict(orient="records")

    # -- 6. delete / free -------------------------------------------------
    async def delete_streams(self, target: str, *, delete_topic: bool = True) -> Any:
        # Release any live tail consumers for the affected topics first.
        for topic in list(self._consumers.keys()):
            if target == "all" or topic == str(target) or topic.endswith(f"_{target}"):
                await asyncio.to_thread(self._stop_consumer, topic)

        def _delete() -> Any:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                return self._streaming().delete_my_stream(
                    target, server=self.server, delete_topic=delete_topic, quiet=True
                )

        try:
            return await asyncio.to_thread(_delete)
        except Exception as exc:
            raise StreamingError(f"Deletion failed: {exc}") from exc


# ---------------------------------------------------------------------------
# SSE sampling (reuses the real api_stream handler so semantics match exactly)
# ---------------------------------------------------------------------------


class _ProfileSource:
    """Minimal stand-in for a SourceResource that the SSE handler expects."""

    def __init__(self, definition: Mapping[str, Any]) -> None:
        self.definition = dict(definition)
        self.id = "profile"


async def _sample_api_stream(
    definition: Mapping[str, Any], *, max_records: int, timeout: float
) -> List[Dict[str, Any]]:
    """Drive the api_stream SSE handler until N records or timeout, collecting them."""
    try:
        from scidx_streaming_v2.streams.runtime.handlers import api_stream as sse_handler
    except Exception as exc:  # pragma: no cover - import dependent
        raise StreamingError(f"api_stream handler unavailable: {exc}") from exc

    records: List[Dict[str, Any]] = []
    stop = asyncio.Event()

    async def _forward(payload: bytes) -> None:
        try:
            records.append(json.loads(payload.decode("utf-8")))
        except Exception:
            return
        if len(records) >= max_records:
            stop.set()

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    source = _ProfileSource(definition)
    try:
        await asyncio.wait_for(
            sse_handler.consume_api_stream_source(
                source=source,
                stop_event=stop,
                forward_message=_forward,
                mark_inactive=_noop,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        stop.set()
    except Exception as exc:
        # Return whatever we gathered; a partial profile still helps.
        logger.warning("SSE profiling ended early: %s", exc)
    return records


# ---------------------------------------------------------------------------
# Profiling helpers
# ---------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Best-effort convert a compiled filter rule to something printable."""
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
            return True
        except ValueError:
            return False
    return False


def _summarize_records(
    records: Sequence[Mapping[str, Any]], *, max_distinct: int = 20
) -> Dict[str, Any]:
    """Per-field profile: non-null count, distinct values (if few), numeric range.

    This is what lets an agent pick real filter values (e.g. vsn == W06C) instead
    of guessing.
    """
    columns: Dict[str, Dict[str, Any]] = {}
    keys: List[str] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        for key in record.keys():
            if key not in columns:
                columns[key] = {"non_null": 0, "_values": []}
                keys.append(key)

    for record in records:
        if not isinstance(record, Mapping):
            continue
        for key in keys:
            value = record.get(key)
            if value is None or value == "":
                continue
            col = columns[key]
            col["non_null"] += 1
            col["_values"].append(value)

    profile: Dict[str, Any] = {}
    for key in keys:
        values = columns[key]["_values"]
        distinct: List[Any] = []
        seen = set()
        numeric_vals: List[float] = []
        for value in values:
            marker = value if isinstance(value, (str, int, float, bool)) else str(value)
            if marker not in seen:
                seen.add(marker)
                if len(distinct) < max_distinct + 1:
                    distinct.append(value)
            if _is_number(value):
                try:
                    numeric_vals.append(float(value))
                except (TypeError, ValueError):
                    pass

        entry: Dict[str, Any] = {
            "non_null": columns[key]["non_null"],
            "distinct_count": len(seen),
        }
        if len(seen) <= max_distinct:
            entry["values"] = [_jsonable(v) for v in distinct[:max_distinct]]
        else:
            entry["sample_values"] = [_jsonable(v) for v in distinct[:8]]
        if numeric_vals and len(numeric_vals) >= max(1, columns[key]["non_null"] // 2):
            entry["numeric_min"] = min(numeric_vals)
            entry["numeric_max"] = max(numeric_vals)
        profile[key] = entry
    return profile
