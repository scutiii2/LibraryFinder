"""Pick the configured catalog client and Laya ranker."""

from __future__ import annotations

from pathlib import Path

from backends.copilot import CopilotClient
from backends.laya import LayaRanker
from settings import configured_backends


STRATEGIES = ("auto", "minicpm", "copilot")


def minicpm_reachable(url: str) -> bool:
    """True when the MiniCPM service answers at all; any HTTP reply counts, only a dead connection does not."""
    from backends.http_json import ServiceError, ServiceUnreachable, request_json
    try:
        request_json("GET", url.rstrip("/") + "/health", timeout=3, retries=0)
    except ServiceUnreachable:
        return False
    except ServiceError:
        pass
    return True


def resolve_strategy(project_root: Path, strategy: str | None = None) -> str:
    """Turn auto (or the configured default) into minicpm or copilot: MiniCPM when its service is up."""
    settings = configured_backends(project_root)
    strategy = strategy or settings["enrichment_backend"]
    if strategy not in STRATEGIES:
        raise ValueError("strategy must be auto, minicpm or copilot")
    if strategy == "auto":
        return "minicpm" if minicpm_reachable(settings["minicpm_url"]) else "copilot"
    return strategy


def enrichment_factory(project_root: Path, strategy: str | None = None):
    """Return a zero-argument callable that builds the catalog client for the strategy (default: configured)."""
    settings = configured_backends(project_root)
    if resolve_strategy(project_root, strategy) == "minicpm":
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
