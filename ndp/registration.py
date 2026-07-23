"""Shared registration core.

Metadata resolution, folder scanning, Sage provenance extraction, and the
ambiguity checks that produce `needs_input`. Both entry points (local path
and Sage query) converge here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import staging
from .models import (
    FieldProvenance,
    GeneralDatasetRequest,
    PendingQuestion,
    RegistrationPreview,
    ResourceRequest,
)
from .staging import StagedRegistration

logger = logging.getLogger(__name__)

DEFAULT_ORG = "sage"
DEFAULT_LICENSE = "cc-by"
MAX_INLINE_RESOURCES = 200

_FORMAT_BY_SUFFIX = {
    ".csv": "CSV",
    ".tsv": "TSV",
    ".json": "JSON",
    ".geojson": "GeoJSON",
    ".txt": "TXT",
    ".nc": "NetCDF",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".tif": "GeoTIFF",
    ".tiff": "GeoTIFF",
    ".parquet": "Parquet",
    ".zip": "ZIP",
    ".pdf": "PDF",
}


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def slugify(text: str, max_len: int = 80) -> str:
    """CKAN dataset names: lowercase alphanumeric, hyphens and underscores."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", text.lower().strip())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    return slug[:max_len] or "dataset"


def unique_slug(base: str, salt: str = "") -> str:
    """Append a short hash so repeated registrations don't collide."""
    digest = hashlib.sha1(f"{base}{salt}".encode()).hexdigest()[:6]
    return f"{slugify(base)}-{digest}"


def detect_format(name: str) -> Optional[str]:
    return _FORMAT_BY_SUFFIX.get(Path(name).suffix.lower())


def detect_mimetype(name: str) -> Optional[str]:
    return mimetypes.guess_type(name)[0]


# ---------------------------------------------------------------------------
# Extras
# ---------------------------------------------------------------------------


def stringify_extras(extras: Dict[str, Any]) -> Dict[str, Any]:
    """CKAN extras round-trip most reliably as flat strings.

    GeneralDatasetRequest.extras allows arbitrary values, but nested dicts can
    be lossy through CKAN's extras table, so serialize them.
    """
    out: Dict[str, Any] = {}
    for key, value in extras.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            out[key] = json.dumps(value, default=str)
        elif isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out


# ---------------------------------------------------------------------------
# Local path scanning
# ---------------------------------------------------------------------------


def scan_path(
    path: str, mode: str = "auto"
) -> Tuple[List[ResourceRequest], List[PendingQuestion], List[str]]:
    """Turn a file or folder into resources, flagging ambiguity."""
    p = Path(path).expanduser().resolve()
    questions: List[PendingQuestion] = []
    warnings: List[str] = []

    if not p.exists():
        raise FileNotFoundError(f"Path does not exist: {p}")

    if p.is_file():
        return [_file_resource(p, p.parent)], [], []

    files = sorted(f for f in p.rglob("*") if f.is_file() and not f.name.startswith("."))
    if not files:
        raise ValueError(f"No files found under {p}")

    suffixes = {f.suffix.lower() for f in files}
    if mode == "auto" and len(suffixes) > 1:
        by_type: Dict[str, int] = {}
        for f in files:
            by_type[f.suffix.lower() or "(none)"] = by_type.get(f.suffix.lower() or "(none)", 0) + 1
        breakdown = ", ".join(f"{n}{ext}" for ext, n in sorted(by_type.items()))
        questions.append(
            PendingQuestion(
                field="mode",
                prompt=f"This folder has mixed file types ({breakdown}). Register as one dataset?",
                options=["one dataset, all files", "split by file type"],
            )
        )

    if len(files) > MAX_INLINE_RESOURCES:
        warnings.append(
            f"{len(files)} files found; only the first {MAX_INLINE_RESOURCES} "
            "will be registered as resources. Consider bundling."
        )
        files = files[:MAX_INLINE_RESOURCES]

    return [_file_resource(f, p) for f in files], questions, warnings


def _file_resource(f: Path, root: Path) -> ResourceRequest:
    try:
        rel = f.relative_to(root).as_posix()
    except ValueError:
        rel = f.name
    return ResourceRequest(
        # The NDP general-dataset contract accepts absolute local paths. Keep
        # the path literal so a local-catalog resource points at the same file
        # the MCP server scanned, instead of rewriting it as a file:// URI.
        url=str(f),
        name=rel,
        format=detect_format(f.name),
        mimetype=detect_mimetype(f.name),
        size=f.stat().st_size,
        description=None,
    )


# ---------------------------------------------------------------------------
# Sage provenance
# ---------------------------------------------------------------------------


def sage_provenance(
    filter_params: Dict[str, Any],
    start: str,
    end: Optional[str],
    df: Any = None,
) -> Dict[str, Any]:
    """Build the sage:* extras block from a query and its result frame."""
    extras: Dict[str, Any] = {
        "sage:query": filter_params,
        "sage:time_range": [start, end],
        "storage_mode": "reference",
        "storage_host": "storage.sagecontinuum.org",
        "source": "sage_continuum",
    }
    if df is None or getattr(df, "empty", True):
        return extras

    for col, key in (
        ("meta.vsn", "sage:vsn"),
        ("meta.plugin", "sage:plugin"),
        ("meta.sensor", "sage:sensor"),
        ("meta.job", "sage:job"),
        ("name", "sage:measurements"),
    ):
        if col in df.columns:
            values = sorted({str(v) for v in df[col].dropna().unique()})
            if values:
                extras[key] = values[:50]

    if "timestamp" in df.columns and len(df):
        try:
            extras["sage:observed_start"] = str(df["timestamp"].min())
            extras["sage:observed_end"] = str(df["timestamp"].max())
        except Exception:
            pass

    extras["sage:record_count"] = int(len(df))
    return extras


def beehive_resources(df: Any, limit: int = MAX_INLINE_RESOURCES) -> List[ResourceRequest]:
    """Extract Beehive upload URLs from a Sage query frame.

    Upload records carry the storage URL in `value`. Registered by reference —
    nothing is copied.
    """
    if df is None or getattr(df, "empty", True) or "value" not in df.columns:
        return []

    resources: List[ResourceRequest] = []
    seen: set[str] = set()

    for _, row in df.iterrows():
        value = row.get("value")
        if not isinstance(value, str) or not value.startswith("https://storage.sagecontinuum.org/"):
            continue
        if value in seen:
            continue
        seen.add(value)

        filename = value.rstrip("/").split("/")[-1] or f"resource-{len(resources)}"
        vsn = row.get("meta.vsn")
        ts = row.get("timestamp")
        desc_parts = [p for p in (f"node {vsn}" if vsn else None, str(ts) if ts else None) if p]

        resources.append(
            ResourceRequest(
                url=value,
                name=f"{vsn}_{filename}" if vsn else filename,
                format=detect_format(filename),
                mimetype=detect_mimetype(filename),
                description=", ".join(desc_parts) or None,
            )
        )
        if len(resources) >= limit:
            break

    return resources


# ---------------------------------------------------------------------------
# Preview assembly
# ---------------------------------------------------------------------------


def build_preview(
    *,
    source: str,
    title: Optional[str],
    resources: List[ResourceRequest],
    extras: Dict[str, Any],
    notes: Optional[str] = None,
    owner_org: Optional[str] = None,
    tags: Optional[List[str]] = None,
    license_id: Optional[str] = None,
    private: bool = True,
    questions: Optional[List[PendingQuestion]] = None,
    warnings: Optional[List[str]] = None,
    name: Optional[str] = None,
) -> RegistrationPreview:
    """Resolve metadata, stage it, and return a dry-run preview."""
    questions = list(questions or [])
    warnings = list(warnings or [])
    provenance: List[FieldProvenance] = []
    assumptions: List[str] = []

    # -- title
    if title:
        provenance.append(FieldProvenance(field="title", source="argument", value=title))
    else:
        title = _infer_title(source, extras, resources)
        provenance.append(FieldProvenance(field="title", source="inferred", value=title))
        questions.append(
            PendingQuestion(
                field="title",
                prompt=f"No title given. Use inferred title {title!r}?",
                options=["yes, use it", "let me provide one"],
            )
        )

    # -- org
    if owner_org:
        provenance.append(FieldProvenance(field="owner_org", source="argument", value=owner_org))
    else:
        owner_org = DEFAULT_ORG
        provenance.append(FieldProvenance(field="owner_org", source="default", value=owner_org))
        assumptions.append(f"owner_org defaulted to {DEFAULT_ORG!r}")

    # -- license
    if license_id:
        provenance.append(FieldProvenance(field="license_id", source="argument", value=license_id))
    else:
        license_id = DEFAULT_LICENSE
        provenance.append(FieldProvenance(field="license_id", source="default", value=license_id))
        assumptions.append(f"license defaulted to {DEFAULT_LICENSE!r}")

    # -- tags
    if tags:
        provenance.append(FieldProvenance(field="tags", source="argument"))
    else:
        tags = _infer_tags(source, extras)
        provenance.append(FieldProvenance(field="tags", source="inferred", value=",".join(tags)))

    # -- notes
    if notes:
        provenance.append(FieldProvenance(field="notes", source="argument"))
    elif source == "sage":
        notes = _sage_notes(extras, len(resources))
        provenance.append(FieldProvenance(field="notes", source="sage_query"))

    # -- name / slug
    resolved_name = name or unique_slug(title, salt=str(len(resources)))

    if private is False and any(p.source == "inferred" for p in provenance):
        questions.append(
            PendingQuestion(
                field="private",
                prompt="Publishing publicly with inferred metadata. Confirm the fields above are correct?",
                options=["yes, register public", "keep it private for now"],
            )
        )

    if not resources:
        warnings.append("No resources resolved — the dataset would be empty.")

    reg = StagedRegistration(
        source=source,
        name=resolved_name,
        title=title,
        owner_org=owner_org,
        resources=resources,
        notes=notes,
        tags=tags,
        license_id=license_id,
        private=private,
        extras=extras,
        provenance=provenance,
        questions=questions,
        assumptions=assumptions,
        warnings=warnings,
    )
    sid = staging.put(reg)

    return RegistrationPreview(
        status="needs_input" if questions else "ready",
        staged_id=sid,
        name=resolved_name,
        title=title,
        owner_org=owner_org,
        notes=notes,
        tags=tags,
        license_id=license_id,
        private=private,
        resource_count=len(resources),
        resources=resources[:20],
        extras=extras,
        provenance=provenance,
        questions=questions,
        assumptions=assumptions,
        warnings=warnings,
    )


def preview_from_staged(sid: str, reg: StagedRegistration) -> RegistrationPreview:
    return RegistrationPreview(
        status="needs_input" if reg.questions else "ready",
        staged_id=sid,
        name=reg.name,
        title=reg.title,
        owner_org=reg.owner_org,
        notes=reg.notes,
        tags=reg.tags,
        license_id=reg.license_id,
        private=reg.private,
        resource_count=len(reg.resources),
        resources=reg.resources[:20],
        extras=reg.extras,
        provenance=reg.provenance,
        questions=reg.questions,
        assumptions=reg.assumptions,
        warnings=reg.warnings,
    )


def to_request(reg: StagedRegistration) -> GeneralDatasetRequest:
    return GeneralDatasetRequest(
        name=reg.name,
        title=reg.title,
        owner_org=reg.owner_org,
        notes=reg.notes,
        tags=reg.tags or None,
        extras=stringify_extras(reg.extras) or None,
        resources=reg.resources or None,
        private=reg.private,
        license_id=reg.license_id,
    )


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------


def _infer_title(source: str, extras: Dict[str, Any], resources: List[ResourceRequest]) -> str:
    if source == "sage":
        vsns = extras.get("sage:vsn") or []
        plugins = extras.get("sage:plugin") or []
        if not plugins:
            query = extras.get("sage:query") or {}
            if isinstance(query, dict) and query.get("plugin"):
                plugins = [query["plugin"]]
        bits = []
        if plugins:
            bits.append(str(plugins[0]).strip(".*"))
        if vsns:
            bits.append(f"node {', '.join(map(str, vsns[:3]))}")
        tr = extras.get("sage:time_range") or []
        if tr and tr[0]:
            bits.append(str(tr[0]))
        if bits:
            return "Sage data: " + " — ".join(bits)
        return "Sage Continuum dataset"
    if resources:
        return Path(resources[0].name).stem.replace("_", " ").replace("-", " ").title()
    return "Untitled dataset"


def _infer_tags(source: str, extras: Dict[str, Any]) -> List[str]:
    tags = []
    if source == "sage":
        tags += ["sage", "sage-continuum", "edge-computing"]
        for vsn in (extras.get("sage:vsn") or [])[:3]:
            tags.append(slugify(str(vsn)))
    return tags


def _sage_notes(extras: Dict[str, Any], n_resources: int) -> str:
    query = extras.get("sage:query")
    tr = extras.get("sage:time_range") or [None, None]
    lines = [
        "Registered from the Sage Continuum via the Sage MCP server.",
        "",
        f"Query filter: {json.dumps(query) if query else 'n/a'}",
        f"Time range: {tr[0]} to {tr[1] or 'now'}",
        f"Resources: {n_resources}",
    ]
    if n_resources:
        lines += [
            "",
            "Data files are referenced in place on Sage storage (Beehive) and are not "
            "copied into this catalog. Fetch with your own Sage portal credentials:",
            "",
            "    curl -u <portal-username>:<portal-access-token> -O <resource-url>",
        ]
    return "\n".join(lines)
