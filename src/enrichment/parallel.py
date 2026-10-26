"""Separate thread-owned Copilot clients with serialized catalog checkpoints."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Lock

from enrichment.pipeline import enrich_catalog, EnrichmentSummary


def enrich_parallel(scanned, previous, client_factory, workers, *, on_checkpoint=None,
                    on_progress=None, on_error=None, on_worker=None, cancel_event=None):
    if not 1 <= workers <= 5:
        raise ValueError("workers must be from 1 through 5")
    lock = Lock()
    records = {(m.file, m.qualified_name): m for m in scanned.methods}
    completed = set()
    # Prime the snapshot with unchanged explanations so another worker's first
    # checkpoint cannot temporarily erase them.
    old = {(m.file, m.qualified_name): m for m in previous.methods} if previous else {}
    for key, method in list(records.items()):
        prior = old.get(key)
        if prior and prior.fingerprint == method.fingerprint and prior.enrichment_status == "success" and prior.enrichment:
            records[key] = replace(method, enrichment=prior.enrichment, enrichment_status="success")
    def snapshot():
        return replace(scanned, methods=tuple(records[(m.file, m.qualified_name)] for m in scanned.methods))
    def checkpoint(partial):
        with lock:
            for m in partial.methods: records[(m.file, m.qualified_name)] = m
            if on_checkpoint: on_checkpoint(snapshot())
    def progress(current, total, method, status):
        with lock:
            completed.add((method.file, method.qualified_name))
            if on_progress: on_progress(len(completed), len(scanned.methods), method, status)
    def error(method, reason):
        with lock:
            if on_error: on_error(method, reason)
    partitions = [scanned.methods[index::workers] for index in range(workers)]
    def work(worker_id, methods):
        # Construct, operate, and dispose Playwright objects on this same thread.
        client = client_factory()
        count = 0
        result = None
        def emit(status, method=None):
            if on_worker:
                with lock:
                    on_worker({"worker_id": worker_id, "current": count,
                               "total": len(methods), "status": status,
                               "pending": result[1].pending if result else max(0, len(methods) - count),
                               "method": method.qualified_name if method else None})
        def report_progress(current, total, method, status):
            nonlocal count
            count = current
            progress(current, total, method, status)
            emit(status, method)
        emit("starting")
        try:
            result = enrich_catalog(replace(scanned, methods=methods), previous, client,
                on_checkpoint=checkpoint, on_progress=report_progress, on_error=error,
                cancel_event=cancel_event,
                on_started=lambda method: emit("processing", method))
            return result
        finally:
            emit("cleanup")
            deleted = client.close()
            status = "cancelled" if cancel_event is not None and cancel_event.is_set() else "interrupted" if result is None or result[1].pending or getattr(client, "interrupted", False) else "finished_with_errors" if result[1].failed else "finished"
            emit(status)
            with lock:
                cleanup.append(deleted)
    cleanup = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, index + 1, methods) for index, methods in enumerate(partitions) if methods]
        results = [future.result() for future in futures]
    summary = EnrichmentSummary(len(scanned.methods),
        **{field: sum(getattr(result[1], field) for result in results)
           for field in ("reused", "enriched", "failed", "pending")})
    return snapshot(), summary, all(cleanup) if cleanup else None
