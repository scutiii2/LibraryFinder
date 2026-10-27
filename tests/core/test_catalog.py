from core.catalog import load_catalog, save_catalog, stale_files
from core.scanner import scan_library
from core.catalog import catalog_health


def test_existing_enriched_catalog_loads_without_class_metadata(tmp_path):
    import json
    from core.models import Enrichment
    saved = {"version": 1, "source_root": str(tmp_path), "files": [], "errors": [],
             "created_at": "2026-10-23T00:00:00", "methods": [{
                 "qualified_name": "libs.find", "module": "libs", "file": "driver.py", "line": 1,
                 "visibility": "public", "owner_class": None, "base_classes": [],
                 "parameters": ["self", "value"], "return_annotation": "bool", "decorators": [],
                 "docstring": None, "imports": [], "keywords": [], "effects": [],
                 "source": "def find(self, value): return True", "fingerprint": "existing-fingerprint",
                 "enrichment_status": "success", "enrichment": {
                     "summary": "Find a value", "purpose": "Lookup", "inputs": ["value"],
                     "outputs": ["bool"], "side_effects": [], "automation_context": "Lookup",
                     "keywords": ["find"], "caveats": []}}]}
    target = tmp_path / "catalog.json"
    target.write_text(json.dumps(saved), encoding="utf-8")
    catalog = load_catalog(target)
    method = catalog.methods[0]
    assert isinstance(method.enrichment, Enrichment)
    assert method.enrichment.inputs == ("value",)
    assert method.enrichment_status == "success"
    assert json.loads(json.dumps(catalog.to_dict()))["methods"] == saved["methods"]


def test_health_detects_new_changed_and_incomplete_methods(library_root):
    catalog = scan_library(library_root)
    (library_root / "utils" / "settings.py").write_text("def updated(): pass\n")
    (library_root / "new.py").write_text("def added(): pass\n")
    health = catalog_health(catalog, library_root)
    assert health["needs_refresh"]
    assert health["changed_files"] == 1
    assert health["new_files"] >= 1
    assert health["incomplete_methods"] == len(catalog.methods)
    assert health["scan_errors"] == 1


def test_saved_catalog_loads_and_detects_changed_source(library_root, tmp_path):
    catalog = scan_library(library_root)
    destination = tmp_path / "catalog.json"
    save_catalog(catalog, destination)

    assert load_catalog(destination) == catalog
    assert stale_files(catalog) == ()

    (library_root / "utils" / "settings.py").write_text("def changed():\n    pass\n")
    assert stale_files(catalog) == (library_root / "utils" / "settings.py",)


def test_save_retries_when_windows_briefly_denies_the_swap(tmp_path, monkeypatch):
    import os
    import core.catalog as module
    real, calls = os.replace, []

    def flaky(source, destination):
        calls.append(1)
        if len(calls) < 3: raise PermissionError("locked")
        return real(source, destination)
    monkeypatch.setattr(module.os, "replace", flaky)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    scanned = scan_library(tmp_path)
    module.save_catalog(scanned, tmp_path / "catalog.json")
    assert len(calls) == 3 and (tmp_path / "catalog.json").exists()
