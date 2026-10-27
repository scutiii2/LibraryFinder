"""JSON-safe records used by the library catalog and recommendations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FileRecord:
    path: str
    sha256: str


@dataclass(frozen=True)
class ScanError:
    path: str
    message: str


@dataclass(frozen=True)
class Enrichment:
    summary: str
    purpose: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    side_effects: tuple[str, ...]
    automation_context: str
    keywords: tuple[str, ...]
    caveats: tuple[str, ...]
    source: str = "ai"
    edited_fingerprint: str = ""


@dataclass(frozen=True)
class MethodRecord:
    qualified_name: str
    module: str
    file: str
    line: int
    visibility: str
    owner_class: str | None
    base_classes: tuple[str, ...]
    parameters: tuple[str, ...]
    return_annotation: str | None
    decorators: tuple[str, ...]
    docstring: str | None
    imports: tuple[str, ...]
    keywords: tuple[str, ...]
    effects: tuple[str, ...]
    source: str = ""
    fingerprint: str = ""
    enrichment: Enrichment | None = None
    enrichment_status: str = "pending"

    @property
    def stale_manual(self) -> bool:
        return bool(self.enrichment and self.enrichment.source == "manual"
                    and self.enrichment.edited_fingerprint != self.fingerprint)


@dataclass(frozen=True)
class Recommendation:
    method: MethodRecord
    confidence: float
    reason: str
    setup: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchResult:
    recommendations: tuple[Recommendation, ...]
    warnings: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    used_laya: bool = False


@dataclass(frozen=True)
class Catalog:
    CURRENT_VERSION = 2

    version: int
    source_root: str
    files: tuple[FileRecord, ...]
    methods: tuple[MethodRecord, ...]
    errors: tuple[ScanError, ...]
    created_at: str
    source_roots: tuple[str, ...] = ()

    def method(self, qualified_name: str) -> MethodRecord | None:
        return next((item for item in self.methods if item.qualified_name == qualified_name), None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Catalog":
        return cls(
            version=int(value["version"]),
            source_root=str(value["source_root"]),
            files=tuple(FileRecord(**item) for item in value["files"]),
            methods=tuple(
                MethodRecord(**{
                        **item,
                        "base_classes": tuple(item["base_classes"]),
                        "parameters": tuple(item["parameters"]),
                        "decorators": tuple(item["decorators"]),
                        "imports": tuple(item["imports"]),
                        "keywords": tuple(item["keywords"]),
                        "effects": tuple(item["effects"]),
                        "enrichment": (Enrichment(**{**item["enrichment"], **{key: tuple(item["enrichment"][key]) for key in ("inputs", "outputs", "side_effects", "keywords", "caveats")}}) if item.get("enrichment") else None),
                        "enrichment_status": item.get("enrichment_status", "pending"),
                    })
                for item in value["methods"]
            ),
            errors=tuple(ScanError(**item) for item in value["errors"]),
            created_at=str(value["created_at"]),
            source_roots=tuple(value.get("source_roots", ())),
        )
