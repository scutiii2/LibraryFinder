from dataclasses import replace

from enrichment.pipeline import enrich_catalog
from core.models import Enrichment
from core.scanner import scan_library


class FakeCopilot:
    def __init__(self, fail=()): self.fail, self.calls = set(fail), []
    def enrich(self, method):
        self.calls.append(method.qualified_name)
        if method.qualified_name in self.fail: raise RuntimeError("unavailable")
        return Enrichment("ticket utility", "find tickets", (), (), (), "Jaggaer", ("ticket",), ())


def test_timeout_stops_requests_and_leaves_remaining_methods_pending(library_root):
    from backends.errors import EnrichmentTimeout
    scanned = scan_library(library_root)
    class TimeoutClient:
        calls = 0
        def enrich(self, method):
            self.calls += 1
            raise EnrichmentTimeout("timed out")
    client = TimeoutClient()
    result, summary = enrich_catalog(scanned, None, client)
    assert client.calls == 1
    assert summary.failed == 1
    assert summary.pending == len(scanned.methods) - 1
    assert all(m.enrichment_status == "pending" for m in result.methods[1:])


def test_success_is_saved_before_next_method_is_requested(library_root, tmp_path):
    from core.catalog import save_catalog, load_catalog
    scanned = scan_library(library_root)
    destination = tmp_path / "catalog.json"
    class InspectingClient(FakeCopilot):
        def enrich(self, method):
            if self.calls:
                saved = load_catalog(destination)
                assert saved.methods[0].enrichment_status == "success"
                assert len(saved.methods) == len(scanned.methods)
                raise KeyboardInterrupt()
            return super().enrich(method)
    import pytest
    with pytest.raises(KeyboardInterrupt):
        enrich_catalog(scanned, None, InspectingClient(),
                       on_checkpoint=lambda snapshot: save_catalog(snapshot, destination))
    saved = load_catalog(destination)
    assert saved.methods[0].enrichment_status == "success"
    assert saved.methods[1].enrichment_status == "pending"


def test_cancel_preserves_success_and_does_not_request_next_method(library_root):
    from threading import Event
    scanned = scan_library(library_root)
    event = Event()
    class Client(FakeCopilot):
        def enrich(self, method):
            value = super().enrich(method)
            event.set()
            return value
    client = Client()
    snapshots = []
    result, summary = enrich_catalog(scanned, None, client, cancel_event=event, on_checkpoint=snapshots.append)
    assert len(client.calls) == 1
    assert summary.enriched == 1
    assert summary.failed == 0
    assert summary.pending == len(scanned.methods) - 1
    assert result.methods[0].enrichment_status == "success"
    assert snapshots[-1] == result


def test_failure_reports_safe_reason_without_exception_content(library_root):
    scanned = scan_library(library_root)
    client = FakeCopilot(fail=[m.qualified_name for m in scanned.methods])
    errors = []
    _, summary = enrich_catalog(scanned, None, client, on_error=lambda method, reason: errors.append(reason))
    assert summary.failed == len(scanned.methods)
    assert errors[0] == "Browser/client failure (RuntimeError)."
    assert "unavailable" not in errors[0]


def test_enrichment_reuses_unchanged_success_and_retries_failed_method(library_root):
    scanned = scan_library(library_root)
    first = FakeCopilot()
    enriched, first_summary = enrich_catalog(scanned, None, first)
    assert first_summary.enriched == len(scanned.methods)
    second = FakeCopilot()
    reused, summary = enrich_catalog(scanned, enriched, second)
    assert summary.reused == len(scanned.methods)
    assert second.calls == []
    failed = replace(enriched, methods=(replace(enriched.methods[0], enrichment=None, enrichment_status="error"), *enriched.methods[1:]))
    retry = FakeCopilot()
    result, summary = enrich_catalog(scanned, failed, retry)
    assert summary.enriched == 1
    assert retry.calls == [scanned.methods[0].qualified_name]
    assert result.methods[0].enrichment_status == "success"
