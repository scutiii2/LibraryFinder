"""Pick the configured catalog client and Laya ranker."""

from __future__ import annotations

from pathlib import Path

from backends.copilot import CopilotClient
from backends.laya import LayaRanker
from settings import configured_backends


def enrichment_factory(project_root: Path):
    """Return a zero-argument callable that builds the configured catalog client."""
    settings = configured_backends(project_root)
    if settings["enrichment_backend"] == "minicpm":
        from backends.minicpm import MiniCPMClient
        return lambda: MiniCPMClient(settings["minicpm_url"])
    return CopilotClient


def ranker_factory(project_root: Path):
    """Return the Laya ranker class or factory (accepts on_evaluation=) for the configured backend."""
    settings = configured_backends(project_root)
    if settings["laya_backend"] == "service":
        from backends.laya import LayaServiceRanker
        return lambda on_evaluation=None: LayaServiceRanker(settings["laya_url"], on_evaluation=on_evaluation)
    return LayaRanker
