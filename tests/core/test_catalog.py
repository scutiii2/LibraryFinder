from library_finder.core.catalog import load_catalog, save_catalog, stale_files
from library_finder.core.scanner import scan_library
from library_finder.core.catalog import catalog_health


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
