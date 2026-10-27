"""Persistence and freshness checks for AST catalogs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from core.models import Catalog


def _replace(source: Path, destination: Path, attempts: int = 20, pause: float = 0.25) -> None:
    """os.replace, retried: on Windows a scanner or indexer briefly holding the file open denies the swap."""
    for attempt in range(attempts):
        try:
            return os.replace(source, destination)
        except PermissionError:
            if attempt == attempts - 1: raise
            time.sleep(pause)


def save_catalog(catalog: Catalog, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(catalog.to_dict(), output, indent=2, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        _replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists(): temporary.unlink()


def load_catalog(path: Path) -> Catalog:
    return Catalog.from_dict(json.loads(path.read_text(encoding="utf-8")))


def catalog_health(catalog: Catalog | None, source) -> dict:
    reasons = []
    counts = {"changed_files": 0, "new_files": 0, "scan_errors": 0, "incomplete_methods": 0, "stale_manual": 0}
    if catalog is None:
        reasons.append("No catalog exists for this library.")
    else:
        counts["changed_files"] = len(stale_files(catalog))
        roots = (source,) if isinstance(source, Path) else tuple(source)
        known = {str((Path(catalog.source_root) / item.path).resolve()) for item in catalog.files}
        counts["new_files"] = len({str(p.resolve()) for root in roots for p in root.rglob("*.py")} - known)
        counts["scan_errors"] = len(catalog.errors)
        counts["incomplete_methods"] = sum(m.enrichment_status != "success" or m.enrichment is None for m in catalog.methods)
        counts["stale_manual"] = sum(m.stale_manual for m in catalog.methods)
        if counts["stale_manual"]:
            reasons.append(f'{counts["stale_manual"]} manual descriptions: source changed since this description was written')
        for key, label in (("changed_files", "changed, deleted, or unreadable files"), ("new_files", "uncataloged Python files"), ("scan_errors", "scan errors"), ("incomplete_methods", "methods awaiting MiniCPM enrichment")):
            if counts[key]: reasons.append(f"{counts[key]} {label}")
    return {"ready": not reasons, "needs_refresh": bool(reasons), "reasons": reasons, **counts}


def stale_files(catalog: Catalog) -> tuple[Path, ...]:
    root = Path(catalog.source_root)
    stale: list[Path] = []
    for record in catalog.files:
        path = root / record.path
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            stale.append(path)
            continue
        if digest != record.sha256:
            stale.append(path)
    return tuple(stale)
