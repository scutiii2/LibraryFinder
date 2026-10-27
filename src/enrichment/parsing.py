"""Strict JSON enrichment decoding and field normalization."""
import json
from backends.errors import EnrichmentResponseError
from core.models import Enrichment

REQUIRED = {"summary", "purpose", "inputs", "outputs", "side_effects", "automation_context", "keywords", "caveats"}


def decode_reply(reply):
    text = reply.strip()
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip().lower() in ("```json", "```") and lines[-1].strip() == "```":
        text = "\n".join(lines[1:-1]).strip()
    return json.loads(text)


def parse_enrichment(reply: str) -> Enrichment:
    try: value = decode_reply(reply)
    except json.JSONDecodeError as exc: raise EnrichmentResponseError("Enrichment did not return JSON") from exc
    if not isinstance(value, dict):
        raise EnrichmentResponseError("Enrichment response must be a JSON object")
    missing = REQUIRED - set(value)
    if missing:
        raise EnrichmentResponseError("Missing fields: " + ", ".join(sorted(missing)))
    for field in ("inputs", "outputs", "side_effects", "keywords", "caveats"):
        item = value[field]
        if item is None:
            item = []
        elif isinstance(item, str):
            item = [item] if item.strip() else []
        elif isinstance(item, dict) and all(isinstance(text, str) for text in item.values()):
            item = [f"{name}: {text}" for name, text in item.items()]
        if not isinstance(item, list) or any(not isinstance(text, str) for text in item):
            raise EnrichmentResponseError(f"Invalid field type: {field}; expected text, text list, or description object")
        value[field] = item
    if any(not isinstance(value[x], str) for x in ("summary", "purpose", "automation_context")):
        raise EnrichmentResponseError("Enrichment response has invalid text fields")
    return Enrichment(value["summary"], value["purpose"], tuple(value["inputs"]), tuple(value["outputs"]), tuple(value["side_effects"]), value["automation_context"], tuple(value["keywords"]), tuple(value["caveats"]))

