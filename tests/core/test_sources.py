from core.sources import scan_sources, normalize_sources
from core.catalog import save_catalog, load_catalog, catalog_health, stale_files
from cli import main
from settings import configured_sources


def test_two_same_named_libraries_remain_distinct_and_round_trip(tmp_path):
    roots = [tmp_path / "a" / "libs", tmp_path / "b" / "libs"]
    for root in roots:
        root.mkdir(parents=True)
        (root / "utility.py").write_text("def lookup(): return 1\n")
    catalog = scan_sources(roots)
    assert len(catalog.methods) == 2
    assert len({m.qualified_name for m in catalog.methods}) == 2
    assert len({m.file for m in catalog.methods}) == 2
    path = tmp_path / "catalog.json"
    save_catalog(catalog, path)
    assert load_catalog(path) == catalog
    assert catalog_health(catalog, roots)["new_files"] == 0
    (roots[1] / "new.py").write_text("def added(): pass\n")
    assert catalog_health(catalog, roots)["new_files"] == 1
    (roots[0] / "utility.py").write_text("def lookup(): return 2\n")
    assert len(stale_files(catalog)) == 1


def test_overlapping_roots_are_scanned_once(tmp_path):
    child = tmp_path / "libs"
    child.mkdir()
    (child / "utility.py").write_text("def lookup(): pass\n")
    assert normalize_sources([child, tmp_path, child]) == (tmp_path,)
    assert len(scan_sources([tmp_path, child]).methods) == 1


def test_cli_accepts_multiple_sources(tmp_path):
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        root.mkdir()
        (root / "util.py").write_text("def lookup(): pass\n")
    target = tmp_path / "catalog.json"
    assert main(["catalog", "--no-enrichment", "--source", *map(str, roots), "--catalog", str(target)]) == 0
    assert len(load_catalog(target).methods) == 2
    (tmp_path / "data").mkdir(exist_ok=True); (tmp_path / "data" / "library-source.txt").write_text("\n".join(map(str, roots)))
    assert configured_sources(tmp_path) == tuple(roots)
