"""Runtime compatibility shims for the Kafka client.

kafka-python 3.0.9 has a bug in ``ClusterAdminMixin._describe_cluster``: it does
``metadata.pop('endpoint_type')`` unconditionally, but brokers whose
``DescribeClusterResponse`` omits that field (older / standard Kafka builds)
make it raise ``KeyError: 'endpoint_type'``. That error surfaces while
``StreamingClient`` initializes its Kafka admin connection, leaving
``kafka_connection = None`` — after which every derived-stream creation fails
with the misleading "Kafka connection is unavailable".

The very next line in the library already uses the safe form
(``metadata.pop('throttle_time_ms', None)``); this shim replaces the method with
an otherwise-identical copy that pops ``endpoint_type`` the same tolerant way.

This is applied at import time from ``streaming.client`` — before any
``KafkaAdminClient`` is constructed — and is idempotent. It touches no
dependency or vendored file on disk.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PATCH_FLAG = "_scidx_endpoint_type_patch"


def apply_patches() -> None:
    """Idempotently patch kafka-python's buggy ``_describe_cluster`` if present."""
    try:
        from kafka.admin import _cluster as cluster_mod
    except Exception:  # pragma: no cover - kafka layout/version dependent
        return

    mixin = getattr(cluster_mod, "ClusterAdminMixin", None)
    if mixin is None:
        return
    if getattr(mixin, _PATCH_FLAG, False):
        return

    original = getattr(mixin, "_describe_cluster", None)
    # Only patch the known-buggy signature; if the library is fixed/changed,
    # leave it alone.
    if original is None:
        return

    Errors = getattr(cluster_mod, "Errors", None)
    DescribeClusterRequest = getattr(cluster_mod, "DescribeClusterRequest", None)
    if Errors is None or DescribeClusterRequest is None:
        return

    async def _describe_cluster_safe(self):  # mirrors the library, safe pop
        try:
            request = DescribeClusterRequest(
                include_cluster_authorized_operations=True,
                include_fenced_brokers=True,
            )
            response = await self._manager.send(request)
            error_type = Errors.for_code(response.error_code)
            if error_type is not Errors.NoError:
                raise error_type(response.error_message)
            metadata = response.to_dict()
            metadata.pop("error_code", None)
            metadata.pop("error_message", None)
            metadata.pop("endpoint_type", None)  # <-- the fix
            metadata.pop("throttle_time_ms", None)
            self._process_acl_operations(metadata)
            return metadata
        except Errors.IncompatibleBrokerVersion:
            # On older brokers fall back to MetadataRequest w/o topics.
            metadata = await self._get_cluster_metadata([])
            metadata.pop("topics", None)
            metadata.pop("throttle_time_ms", None)
            for broker in metadata.get("brokers", []):
                if "node_id" in broker:
                    broker["broker_id"] = broker.pop("node_id")
            return metadata

    setattr(mixin, "_describe_cluster", _describe_cluster_safe)
    setattr(mixin, _PATCH_FLAG, True)
    logger.info("Applied kafka-python endpoint_type compatibility patch.")
