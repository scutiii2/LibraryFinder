"""Deterministic retrieval over catalog metadata."""

from __future__ import annotations

import re

from core.models import Catalog, MethodRecord

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _tokens(value: str) -> set[str]:
    return {word.lower() for word in _WORD.findall(value.replace("_", " "))}


def retrieve(goal: str, catalog: Catalog, limit: int = 12) -> list[MethodRecord]:
    """Return metadata matches without invoking an AI model or source code."""
    requested = _tokens(goal)

    def score(record: MethodRecord) -> tuple[float, str]:
        enrichment = record.enrichment if record.enrichment_status == "success" else None
        semantic = (enrichment.summary, enrichment.purpose, enrichment.automation_context, *enrichment.keywords, *enrichment.caveats) if enrichment else ()
        fields = " ".join((record.qualified_name, *record.parameters, record.docstring or "", *record.keywords, *record.imports, *record.effects, *semantic))
        matched = requested & _tokens(fields)
        value = float(len(matched))
        if record.visibility == "public":
            value += 0.1
        return value, record.qualified_name

    return sorted(catalog.methods, key=lambda record: score(record), reverse=True)[:limit]
