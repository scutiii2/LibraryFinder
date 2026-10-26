"""Incremental semantic enrichment without changing AST facts."""
from dataclasses import dataclass, replace
from typing import Protocol

from library_finder.models import Catalog, CopilotEnrichment, MethodRecord


class CopilotClient(Protocol):
    def enrich(self, method: MethodRecord) -> CopilotEnrichment: ...


@dataclass(frozen=True)
class EnrichmentSummary:
    scanned: int
    reused: int = 0
    enriched: int = 0
    failed: int = 0
    pending: int = 0


def enrich_catalog(scanned: Catalog, previous: Catalog | None, client: CopilotClient, on_progress=None, on_error=None, on_checkpoint=None, on_started=None, cancel_event=None) -> tuple[Catalog, EnrichmentSummary]:
    if client is not None and cancel_event is not None: client.cancel_event = cancel_event
    old = {(item.file, item.qualified_name): item for item in previous.methods} if previous else {}
    methods = []; reused = enriched = failed = pending = 0
    interrupted = False
    batch_results = {}
    def reusable(method):
        prior = old.get((method.file, method.qualified_name))
        return prior and prior.fingerprint == method.fingerprint and prior.enrichment_status == "success" and prior.enrichment
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
        if interrupted or client is None or (cancel_event is not None and cancel_event.is_set() and method.qualified_name not in batch_results):
            methods.append(replace(method, enrichment=None, enrichment_status="pending")); pending += 1
            checkpoint()
            continue
        try:
            if on_started: on_started(method)
            if hasattr(client, "enrich_batch"):
                if method.qualified_name not in batch_results:
                    batch = []
                    chars = 0
                    for candidate in scanned.methods[len(methods):]:
                        if reusable(candidate): continue
                        size = len(candidate.source[:12000])
                        if batch and (len(batch) == 5 or chars + size > 24000): break
                        batch.append(candidate); chars += size
                    batch_results.update(client.enrich_batch(tuple(batch)))
                from library_finder.copilot import CopilotResponseError
                value = batch_results.pop(method.qualified_name, CopilotResponseError("Copilot omitted this method from the batch"))
                if isinstance(value, CopilotResponseError) and "omitted" in str(value):
                    # One single-method retry for a method Copilot left out of the batch.
                    value = client.enrich(method)
                if isinstance(value, Exception): raise value
            else:
                value = client.enrich(method)
            methods.append(replace(method, enrichment=value, enrichment_status="success")); enriched += 1
        except Exception as error:
            from library_finder.copilot import CopilotCancelled
            if isinstance(error, CopilotCancelled):
                interrupted = True
                methods.append(replace(method, enrichment=None, enrichment_status="pending")); pending += 1
                checkpoint()
                continue
            from library_finder.copilot import CopilotTimeout
            if isinstance(error, CopilotTimeout) or getattr(client, "interrupted", False):
                interrupted = True
            methods.append(replace(method, enrichment=None, enrichment_status="error")); failed += 1
            if on_error:
                from library_finder.copilot import CopilotUnavailable, CopilotResponseError
                # Browser exceptions can contain prompt text. Expose only controlled messages.
                reason = str(error) if isinstance(error, (CopilotUnavailable, CopilotResponseError)) else f"Browser/client failure ({type(error).__name__})."
                on_error(method, reason)
        checkpoint()
        if on_progress: on_progress(len(methods), len(scanned.methods), method, methods[-1].enrichment_status)
    return replace(scanned, methods=tuple(methods)), EnrichmentSummary(len(methods), reused, enriched, failed, pending)
