"""In-memory staging store for two-phase dataset registration.

Holds a prepared registration between `prepare` and `finalize` so that
answering a question or confirming a preview doesn't require re-scanning a
folder or re-running a Sage query.

Deliberately not persistent. These are ephemeral handles, not state.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import FieldProvenance, PendingQuestion, ResourceRequest

_TTL_SECONDS = 3600


@dataclass
class StagedRegistration:
    source: str  # "sage" | "local"
    name: str
    title: str
    owner_org: str
    resources: List[ResourceRequest] = field(default_factory=list)
    notes: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    license_id: Optional[str] = None
    private: bool = True
    extras: Dict[str, Any] = field(default_factory=dict)
    provenance: List[FieldProvenance] = field(default_factory=list)
    questions: List[PendingQuestion] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # Original local path -> returned Drive metadata. Entries are written as
    # each upload succeeds so an NDP failure can be retried without duplicates.
    drive_uploads: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


_store: Dict[str, StagedRegistration] = {}


def _sweep() -> None:
    now = time.time()
    expired = [k for k, v in _store.items() if now - v.created_at > _TTL_SECONDS]
    for k in expired:
        del _store[k]


def put(reg: StagedRegistration) -> str:
    _sweep()
    sid = f"stg_{uuid.uuid4().hex[:8]}"
    _store[sid] = reg
    return sid


def get(sid: str) -> Optional[StagedRegistration]:
    _sweep()
    return _store.get(sid)


def update(sid: str, **fields: Any) -> Optional[StagedRegistration]:
    reg = get(sid)
    if reg is None:
        return None
    for key, value in fields.items():
        if hasattr(reg, key):
            setattr(reg, key, value)
    return reg


def drop(sid: str) -> None:
    _store.pop(sid, None)


def clear() -> None:
    _store.clear()