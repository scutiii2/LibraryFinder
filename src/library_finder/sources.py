"""Normalize library roots and merge independent AST catalogs."""
import hashlib
from dataclasses import replace
from pathlib import Path

from library_finder.scanner import scan_library


def normalize_sources(values):
    roots = list(dict.fromkeys(Path(value).expanduser().resolve() for value in values))
    if not roots: raise ValueError("Enter at least one library directory.")
    for root in roots:
        if not root.is_dir(): raise ValueError(f"Library directory does not exist: {root}")
    # A selected ancestor already includes its nested selected directory.
    return tuple(root for root in roots if not any(other in root.parents for other in roots if other != root))


def catalog_roots(catalog):
    return tuple(Path(value).resolve() for value in (catalog.source_roots or (catalog.source_root,)))


def scan_sources(roots, scanner=scan_library):
    roots = normalize_sources(roots)
    if len(roots) == 1: return scanner(roots[0])
    catalogs = [scanner(root) for root in roots]
    methods, files, errors = [], [], []
    for root, catalog in zip(roots, catalogs):
        namespace = "library_" + hashlib.sha256(str(root).encode()).hexdigest()[:12]
        methods.extend(replace(m, file=str((root / m.file).resolve()),
            module=f"{namespace}.{m.module}", qualified_name=f"{namespace}.{m.qualified_name}") for m in catalog.methods)
        files.extend(replace(f, path=str((root / f.path).resolve())) for f in catalog.files)
        errors.extend(replace(e, path=str((root / e.path).resolve())) for e in catalog.errors)
    return replace(catalogs[0], source_root=str(roots[0]), source_roots=tuple(map(str, roots)),
                   methods=tuple(methods), files=tuple(files), errors=tuple(errors))
