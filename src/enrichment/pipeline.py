"""Incremental semantic enrichment without changing AST facts."""
from dataclasses import dataclass, replace
from typing import Protocol

from core.models import Catalog, Enrichment, MethodRecord


class EnrichmentClient(Protocol):
    def enrich(self, method: MethodRecord) -> Enrichment: ...


@dataclass(frozen=True)
class EnrichmentSummary:
    scanned: int
    reused: int = 0
    enriched: int = 0
    failed: int = 0
    pending: int = 0


def enrich_catalog(scanned: Catalog, previous: Catalog | None, client: EnrichmentClient, on_progress=None, on_error=None, on_checkpoint=None, on_started=None, cancel_event=None) -> tuple[Catalog, EnrichmentSummary]:
    if client is not None and cancel_event is not None: client.cancel_event = cancel_event
    old = {(item.file, item.qualified_name): item for item in previous.methods} if previous else {}
    methods = []; reused = enriched = failed = pending = 0
    interrupted = False
    def checkpoint():
        if on_checkpoint:
            # Preserve unchanged successful records even before reaching them.
            remaining = []
            for item in scanned.methods[len(methods):]:
                prior = old.get((item.file, item.qualified_name))
                if prior and prior.fingerprint == item.fingerprint and prior.enrichment_status == "success" and prior.enrichment:
                    item = replace(item, enrichment=prior.enrichment, enrichment_status="success")
                remaining.append(item)
            on_checkpoint(replace(scanned, methods=tuple(methods + remaining)))
    for method in scanned.methods:
        prior = old.get((method.file, method.qualified_name))
        if prior and prior.fingerprint == method.fingerprint and prior.enrichment_status == "success" and prior.enrichment:
            methods.append(replace(method, enrichment=prior.enrichment, enrichment_status="success")); reused += 1
            checkpoint()
            if on_progress: on_progress(len(methods), len(scanned.methods), method, "reused")
            continue
        if interrupted or client is None or (cancel_event is not None and cancel_event.is_set()):
            methods.append(replace(method, enrichment=None, enrichment_status="pending")); pending += 1
            checkpoint()
            continue
        try:
            if on_started: on_started(method)
            value = client.enrich(method)
            methods.append(replace(method, enrichment=value, enrichment_status="success")); enriched += 1
        except Exception as error:
            from backends.errors import EnrichmentCancelled
            if isinstance(error, EnrichmentCancelled):
                interrupted = True
                methods.append(replace(method, enrichment=None, enrichment_status="pending")); pending += 1
                checkpoint()
                continue
            from backends.errors import EnrichmentTimeout
            if isinstance(error, EnrichmentTimeout) or getattr(client, "interrupted", False):
                interrupted = True
            methods.append(replace(method, enrichment=None, enrichment_status="error")); failed += 1
            if on_error:
                from backends.errors import EnrichmentUnavailable, EnrichmentResponseError
                # Client exceptions can contain prompt text. Expose only controlled messages.
                reason = str(error) if isinstance(error, (EnrichmentUnavailable, EnrichmentResponseError)) else f"Browser/client failure ({type(error).__name__})."
                on_error(method, reason)
        checkpoint()
        if on_progress: on_progress(len(methods), len(scanned.methods), method, methods[-1].enrichment_status)
    return replace(scanned, methods=tuple(methods)), EnrichmentSummary(len(methods), reused, enriched, failed, pending)
