"""Paginated catalog browsing and user-owned descriptions."""
from dataclasses import asdict, replace
from pathlib import Path

from fastapi import HTTPException

from backends.minicpm import scanner_fields
from core.catalog import save_catalog
from core.models import Enrichment


def ensure_idle(app):
    if app.state.catalog_building or (app.state.run and app.state.run["active"]):
        raise HTTPException(409, "Wait for the active build or search to finish.")


def source_preview(app, record):
    source_path = (Path(app.state.source_root).resolve() / record.file).resolve()
    if source_path.suffix != ".py" or any(part.lower().startswith(".env") for part in source_path.parts):
        raise HTTPException(403, "Only Python source previews are supported.")
    if not any(directory.resolve() in source_path.parents for directory in app.state.source_roots):
        raise HTTPException(403, "Source path is outside the configured library.")
    try:
        all_lines = source_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise HTTPException(404, "Source file is unavailable.") from error
    start = max(1, record.line - 3)
    end = min(len(all_lines), record.line + 12)
    return {"path": record.file, "start_line": start, "lines": all_lines[start - 1:end]}


def method_status(method):
    if method.enrichment_status == "success" and method.enrichment:
        return method.enrichment.source
    return "failed" if method.enrichment_status == "error" else "pending"


def method_detail(method):
    if method.enrichment:
        description = asdict(method.enrichment)
    else:
        inputs, outputs, effects = scanner_fields(method)
        description = asdict(Enrichment("", "", tuple(inputs), tuple(outputs), tuple(effects), "", (), ()))
    return {"qualified_name": method.qualified_name, "file": method.file, "line": method.line,
            "status": method_status(method), "stale_manual": method.stale_manual,
            "description": description}


def validate_description(payload):
    clear = payload.get("clear", False)
    if not isinstance(clear, bool):
        raise HTTPException(422, "Clear must be a boolean.")
    if clear:
        return None
    fields = {}
    for key in ("summary", "purpose", "automation_context"):
        value = payload.get(key, "" if key == "automation_context" else None)
        if not isinstance(value, str) or len(value) > 2000 or (key != "automation_context" and not value.strip()):
            raise HTTPException(422, f"{key} must be text of at most 2000 characters; summary and purpose are required.")
        fields[key] = value.strip()
    for key, max_items, max_length in (("keywords", 30, 60), ("caveats", 30, 2000)):
        value = payload.get(key, [])
        if not isinstance(value, list) or len(value) > max_items or any(not isinstance(item, str) or not item.strip() or len(item) > max_length for item in value):
            raise HTTPException(422, f"{key} must contain at most {max_items} non-empty strings of at most {max_length} characters.")
        fields[key] = tuple(item.strip() for item in value)
    return fields


def register_catalog_editor(app, state):
    def find_method(name):
        catalog = app.state.catalog
        method = catalog.method(name) if catalog else None
        if method is None:
            raise HTTPException(404, "Catalog method not found.")
        return method

    @app.get("/api/catalog/methods")
    def methods(query: str = "", status: str = "", offset: int = 0, limit: int = 50):
        if len(query) > 2000 or status not in ("", "ai", "manual", "pending", "failed") or offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(422, "Invalid catalog filter or page (limit must be 1–100).")
        catalog = app.state.catalog
        matches = [m for m in catalog.methods if query.casefold() in m.qualified_name.casefold()
                   and (not status or method_status(m) == status)] if catalog else []
        return {"methods": [method_detail(m) for m in matches[offset:offset + limit]],
                "total": len(matches), "offset": offset, "limit": limit}

    @app.get("/api/catalog/source")
    def source(method: str):
        return source_preview(app, find_method(method))

    @app.put("/api/catalog/methods/{qualified_name:path}/description")
    def description(qualified_name: str, payload: dict):
        with app.state.catalog_lock:
            ensure_idle(app)
            method = find_method(qualified_name)
            fields = validate_description(payload)
            enrichment = None
            if fields is not None:
                inputs, outputs, effects = scanner_fields(method) if method.enrichment is None else (
                    method.enrichment.inputs, method.enrichment.outputs, method.enrichment.side_effects)
                enrichment = Enrichment(**fields, inputs=tuple(inputs), outputs=tuple(outputs),
                                        side_effects=tuple(effects), source="manual", edited_fingerprint=method.fingerprint)
            updated = replace(method, enrichment=enrichment, enrichment_status="success" if enrichment else "pending")
            catalog = replace(app.state.catalog, methods=tuple(updated if item is method else item for item in app.state.catalog.methods))
            save_catalog(catalog, state / "catalog.json")
            app.state.catalog = catalog
            return method_detail(updated)
