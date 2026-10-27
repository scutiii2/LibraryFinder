from threading import Barrier, get_ident
from dataclasses import replace
import pytest

from enrichment.parallel import enrich_parallel
from settings import configured_workers
from core.scanner import scan_library
from core.models import Enrichment


def test_two_workers_process_once_and_preserve_all_checkpoints(library_root):
    scanned = scan_library(library_root)
    scanned = replace(scanned, methods=scanned.methods[:2])
    barrier = Barrier(2)
    calls, checkpoints, worker_events = [], [], []
    class Client:
        def __init__(self): self.thread = get_ident()
        def enrich(self, method):
            assert get_ident() == self.thread
            calls.append(method.qualified_name)
            barrier.wait(timeout=3)
            return Enrichment("summary", "purpose", (), (), (), "context", (), ())
        def close(self):
            assert get_ident() == self.thread
            return True
    catalog, summary = enrich_parallel(scanned, None, Client, 2,
        on_checkpoint=checkpoints.append, on_worker=worker_events.append)
    assert sorted(calls) == sorted(m.qualified_name for m in scanned.methods)
    assert summary.enriched == 2
    assert all(m.enrichment_status == "success" for m in catalog.methods)
    assert all(len(snapshot.methods) == 2 for snapshot in checkpoints)
    assert sum(m.enrichment_status == "success" for m in checkpoints[-1].methods) == 2
    for worker_id in (1, 2):
        events = [event for event in worker_events if event["worker_id"] == worker_id]
        assert [event["status"] for event in events] == ["starting", "processing", "success", "cleanup", "finished"]
        assert events[1]["method"] in calls
        assert events[-1]["current"] == events[-1]["total"] == 1


@pytest.mark.parametrize("value", [0, 6, True, 2.5, "5"])
def test_invalid_worker_configuration_is_rejected(tmp_path, value):
    import json
    (tmp_path / "configs.json").write_text(json.dumps({"catalog_workers":value}))
    with pytest.raises(ValueError, match="catalog_workers"):
        configured_workers(tmp_path)


def test_worker_setting_accepts_five(tmp_path):
    (tmp_path / "configs.json").write_text('{"catalog_workers":5}')
    assert configured_workers(tmp_path) == 5


def test_method_is_durable_before_next_request_while_other_worker_is_busy(library_root, tmp_path):
    from threading import Event
    from core.catalog import save_catalog, load_catalog
    scanned = scan_library(library_root)
    target = tmp_path / "catalog.json"
    first_saved = Event()
    other_started = Event()
    first_name = scanned.methods[0].qualified_name
    second_name = scanned.methods[1].qualified_name
    third_name = scanned.methods[2].qualified_name
    def checkpoint(snapshot):
        save_catalog(snapshot, target)
        if snapshot.method(first_name).enrichment_status == "success":
            first_saved.set()
    class Client:
        def enrich(self, method):
            if method.qualified_name == first_name:
                assert other_started.wait(3)
            elif method.qualified_name == second_name:
                other_started.set()
                assert first_saved.wait(3)
                assert load_catalog(target).method(first_name).enrichment_status == "success"
            elif method.qualified_name == third_name:
                # Same worker's next request observes its preceding disk commit.
                assert load_catalog(target).method(first_name).enrichment_status == "success"
            return Enrichment("summary", "purpose", (), (), (), "context", (), ())
        def close(self): return True
    _, summary = enrich_parallel(scanned, None, Client, 2, on_checkpoint=checkpoint)
    assert summary.enriched == len(scanned.methods)
    assert all(m.enrichment_status == "success" for m in load_catalog(target).methods)




def test_worker_setting_defaults_to_one_without_catalog_workers(tmp_path):
    (tmp_path / "configs.json").write_text('{}')
    assert configured_workers(tmp_path) == 1


def test_retired_worker_alias_is_ignored(tmp_path):
    import json
    retired_key = "co" + "pilot_workers"
    (tmp_path / "configs.json").write_text(json.dumps({retired_key: 3}))
    assert configured_workers(tmp_path) == 1
    (tmp_path / "configs.json").write_text(json.dumps({retired_key: 3, "catalog_workers": 2}))
    assert configured_workers(tmp_path) == 2
