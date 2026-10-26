"""Optional local Laya adapter; it receives only bounded catalog metadata."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Sequence

from library_finder.http_json import ServiceError, ServiceUnreachable, request_json
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

    def _predict(self, text: str, questions: dict) -> dict:
        return self._engine.predict(text, questions)

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
            answer = self._predict(f"Goal: {goal}", questions)["answers"]["fit"]
            confidence = float(answer["noul"])
            recommendations.append({
                "method": item.qualified_name,
                "confidence": confidence,
                "reason": "Laya assessed this method as fitting the goal.",
            })
            if self._on_evaluation is not None:
                self._on_evaluation(current, total, item, confidence)
        return {"recommendations": recommendations}


class LayaServiceRanker(LayaRanker):
    """Ranks through a remote or local laya_service over HTTP; loads no model here."""

    def __init__(self, url: str, token: str | None = None, on_evaluation=None, timeout: float = 120) -> None:
        super().__init__(on_evaluation=on_evaluation)
        self.url = url.rstrip("/")
        self.token = token if token is not None else os.environ.get("LAYA_TOKEN")
        self.timeout = timeout

    def prepare(self):
        """Check the service is up; raise RuntimeError so callers fall back to deterministic results."""
        try:
            request_json("GET", self.url + "/health", timeout=10)
        except (ServiceUnreachable, ServiceError) as error:
            raise RuntimeError(f"Laya service is not reachable at {self.url}") from error
        return self

    def _predict(self, text: str, questions: dict) -> dict:
        # The service defines its own labels and rejects a `labels` field (HTTP 422).
        questions = {name: {key: value for key, value in spec.items() if key != "labels"} for name, spec in questions.items()}
        try:
            return request_json("POST", self.url + "/v1/ask", {"strategy": "auto", "text": text, "questions": questions},
                                token=self.token, timeout=self.timeout)
        except (ServiceUnreachable, ServiceError) as error:
            raise RuntimeError(f"Laya service request failed: {error}") from error
