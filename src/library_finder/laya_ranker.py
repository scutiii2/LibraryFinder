"""Optional local Laya adapter; it receives only bounded catalog metadata."""

from __future__ import annotations

from collections.abc import Callable
from typing import Sequence

from library_finder.models import MethodRecord


class LayaRanker:
    def __init__(self, engine=None, on_evaluation: Callable[[int, int, MethodRecord, float], None] | None = None) -> None:
        self._engine = engine
        self._on_evaluation = on_evaluation

    def prepare(self):
        """Load the checkpoint once so web startup can warm it before serving."""
        if self._engine is None:
            try:
                import laya
            except ImportError as error:
                raise RuntimeError("Laya is not installed") from error
            self._engine = laya.load("convaiinnovations/laya")
        return self._engine

    def rank(self, goal: str, methods: Sequence[MethodRecord]) -> dict:
        self.prepare()
        recommendations = []
        total = len(methods)
        for current, item in enumerate(methods, start=1):
            enrichment = item.enrichment if item.enrichment_status == "success" else None
            semantic = (
                f" Copilot analysis (generated, not source fact): summary={enrichment.summary}; "
                f"purpose={enrichment.purpose}; keywords={enrichment.keywords}."
                if enrichment else ""
            )
            questions = {
                "fit": {
                "type": "noul",
                "instructions": "Does this method fit the goal?",
                "criteria": {
                    "false": f"No: {item.qualified_name} does not fit the goal.",
                    "true": (
                        f"Yes: {item.qualified_name}; signature={item.parameters}; "
                        f"docstring={item.docstring}; effects={item.effects}.{semantic}"
                    ),
                },
                "labels": {"false": "No", "true": "Yes"},
                }
            }
            answer = self._engine.predict(f"Goal: {goal}", questions)["answers"]["fit"]
            confidence = float(answer["noul"])
            recommendations.append({
                "method": item.qualified_name,
                "confidence": confidence,
                "reason": "Laya assessed this method as fitting the goal.",
            })
            if self._on_evaluation is not None:
                self._on_evaluation(current, total, item, confidence)
        return {"recommendations": recommendations}
