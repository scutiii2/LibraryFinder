from dataclasses import replace
from threading import Event

import pytest

from core.catalog import catalog_health
from core.models import Enrichment
from core.scanner import scan_library
from enrichment.pipeline import enrich_catalog
from enrichment.parallel import enrich_parallel


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("overwrite, overwrite_manual, expected", [(False, False, "manual"), (True, False, "manual"), (True, True, "ai"), (False, True, "manual")])
def test_manual_preserved_across_source_changes_and_overwrite(library_root, parallel, overwrite, overwrite_manual, expected):
    scanned = scan_library(library_root)
    manual = Enrichment("My summary", "My purpose", (), (), (), "", (), (), source="manual", edited_fingerprint="old")
    previous = replace(scanned, methods=tuple(replace(m, fingerprint="old", enrichment=manual, enrichment_status="success") for m in scanned.methods))
    class Client:
        def enrich(self, method): return Enrichment("AI", "AI", (), (), (), "", (), ())
        def close(self): pass
    snapshots = []
    kwargs = dict(overwrite=overwrite, overwrite_manual=overwrite_manual, on_checkpoint=snapshots.append)
    result, summary = (enrich_parallel(scanned, previous, Client, 2, **kwargs) if parallel else enrich_catalog(scanned, previous, Client(), **kwargs))
    assert all(m.enrichment.source == expected for m in result.methods)
    if expected == "manual":
        assert summary.reused == len(scanned.methods)
        assert all(m.enrichment.source == "manual" for snapshot in snapshots for m in snapshot.methods)
        health = catalog_health(result, library_root)
        assert health["stale_manual"] == len(scanned.methods)
        assert "source changed" in " ".join(health["reasons"])


def test_cancelled_checkpoint_keeps_unvisited_changed_manual(library_root):
    scanned = scan_library(library_root)
    manual = Enrichment("s", "p", (), (), (), "", (), (), source="manual", edited_fingerprint="old")
    previous = replace(scanned, methods=tuple(replace(m, fingerprint="old", enrichment=manual, enrichment_status="success") for m in scanned.methods[1:]))
    event = Event(); event.set()
    snapshots = []
    result, _ = enrich_catalog(scanned, previous, None, cancel_event=event, on_checkpoint=snapshots.append)
    assert result.methods[0].enrichment_status == "pending"
    assert all(m.enrichment == manual for snapshot in snapshots for m in snapshot.methods[1:])
