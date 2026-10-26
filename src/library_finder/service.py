"""Safe orchestration of deterministic retrieval and optional Laya ranking."""

from __future__ import annotations

from typing import Protocol, Sequence

from library_finder.models import Catalog, MethodRecord, Recommendation, SearchResult
from library_finder.search import retrieve


class Ranker(Protocol):
    def rank(self, goal: str, candidates: Sequence[MethodRecord]) -> dict: ...


def find_methods(
    goal: str,
    catalog: Catalog,
    ranker: Ranker | None = None,
    *,
    raise_ranking_errors: bool = False,
) -> SearchResult:
    candidates = retrieve(goal, catalog)
    fallback = tuple(Recommendation(item, 0.0, "Deterministic metadata match.") for item in candidates)
    if ranker is None:
        return SearchResult(fallback, warnings=("semantic ranking unavailable",))
    try:
        response = ranker.rank(goal, catalog.methods)
        records = {item.qualified_name: item for item in catalog.methods}
        recommendations: list[Recommendation] = []
        warnings: list[str] = []
        for item in response["recommendations"]:
            method = records.get(item["method"])
            if method is None:
                warnings.append("unrecognized model method")
                continue
            confidence = float(item["confidence"])
            if not 0 <= confidence <= 1:
                raise ValueError("invalid confidence")
            recommendations.append(Recommendation(method, confidence, str(item["reason"])))
        return SearchResult(tuple(recommendations), tuple(warnings), tuple(response.get("gaps", ())), True)
    except Exception as error:
        if raise_ranking_errors:
            raise
        return SearchResult(fallback, warnings=(f"semantic ranking unavailable: {error}",))
