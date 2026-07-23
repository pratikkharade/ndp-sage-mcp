"""Pydantic models for NDP dataset registration.

Mirrors the ndp-ep-api OpenAPI contract (v0.33.0).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request-side models (mirror ndp-ep-api schemas)
# ---------------------------------------------------------------------------


class ResourceRequest(BaseModel):
    """A single resource inside a GeneralDatasetRequest."""

    url: str
    name: str
    format: Optional[str] = None
    description: Optional[str] = None
    mimetype: Optional[str] = None
    size: Optional[int] = None


class GeneralDatasetRequest(BaseModel):
    """Payload for POST /dataset."""

    name: str
    title: str
    owner_org: str
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    groups: Optional[List[str]] = None
    extras: Optional[Dict[str, Any]] = None
    resources: Optional[List[ResourceRequest]] = None
    private: Optional[bool] = False
    license_id: Optional[str] = None
    version: Optional[str] = None


# ---------------------------------------------------------------------------
# Tool-facing models
# ---------------------------------------------------------------------------

ProvenanceSource = Literal["argument", "sage_query", "extracted", "inferred", "default"]


class FieldProvenance(BaseModel):
    """Where a resolved metadata field came from."""

    field: str
    source: ProvenanceSource
    value: Optional[str] = None


class PendingQuestion(BaseModel):
    """A question the tool needs answered before it can proceed."""

    field: str
    prompt: str
    options: List[str] = Field(default_factory=list)
    free_text_ok: bool = True


class RegistrationPreview(BaseModel):
    """Dry-run result. Nothing has been written to NDP."""

    status: Literal["ready", "needs_input"]
    staged_id: str
    name: str
    title: str
    owner_org: str
    notes: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    license_id: Optional[str] = None
    private: bool = True
    resource_count: int = 0
    resources: List[ResourceRequest] = Field(default_factory=list)
    extras: Dict[str, Any] = Field(default_factory=dict)
    provenance: List[FieldProvenance] = Field(default_factory=list)
    questions: List[PendingQuestion] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"[{self.status}] {self.title}",
            f"  name:      {self.name}",
            f"  org:       {self.owner_org}",
            f"  private:   {self.private}",
            f"  resources: {self.resource_count}",
        ]
        inferred = [p.field for p in self.provenance if p.source == "inferred"]
        if inferred:
            lines.append(f"  inferred:  {', '.join(inferred)} (review before publishing)")
        for q in self.questions:
            lines.append(f"  ? {q.prompt}")
            if q.options:
                lines.append(f"      options: {' | '.join(q.options)}")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        return "\n".join(lines)


class RegistrationResult(BaseModel):
    """Committed result of a registration."""

    status: Literal["registered", "failed", "partial"]
    dataset_id: Optional[str] = None
    dataset_name: Optional[str] = None
    resources_created: int = 0
    server: Optional[str] = None  # which catalog it was written to: local | pre_ckan
    published_to_pre_ckan: bool = False
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)