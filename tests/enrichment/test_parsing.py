import pytest
from backends.errors import EnrichmentResponseError
from enrichment.parsing import parse_enrichment


def test_failure_types_preserve_interrupt_and_response_categories():
    from backends.errors import EnrichmentUnavailable, EnrichmentTimeout, EnrichmentCancelled
    assert isinstance(EnrichmentTimeout("timed out"), EnrichmentResponseError)
    assert isinstance(EnrichmentCancelled("Catalog build cancelled."), EnrichmentUnavailable)
    assert str(EnrichmentTimeout("timed out")) == "timed out"


def test_parse_enrichment_accepts_only_complete_json_object():
    value = parse_enrichment('{"summary":"s","purpose":"p","inputs":[],"outputs":[],"side_effects":[],"automation_context":"c","keywords":[],"caveats":[]}')
    assert value.purpose == "p"
    with pytest.raises(EnrichmentResponseError):
        parse_enrichment("answer: {}")


def test_parse_enrichment_normalizes_text_lists_and_ignores_extra_metadata():
    import json
    result = parse_enrichment(json.dumps({"summary":"s", "purpose":"p", "inputs":"self", "outputs":"bool", "side_effects":None, "automation_context":"c", "keywords":["cache"], "caveats":[], "confidence":0.8}))
    assert result.inputs == ("self",)
    assert result.outputs == ("bool",)
    assert result.side_effects == ()


def test_parse_enrichment_reports_missing_keys_without_source_values():
    with pytest.raises(EnrichmentResponseError, match="Missing fields:.*purpose"):
        parse_enrichment('{"summary":"private code"}')


def test_parse_enrichment_accepts_parameter_descriptions_from_description_object():
    import json
    result = parse_enrichment(json.dumps({"summary":"Stores cache", "purpose":"Set cache entry", "inputs":{"self":"BaseDriver instance", "key":"Cache key", "value":"Value to store"}, "outputs":"No explicit return; None", "side_effects":["Mutates self.CACHE"], "automation_context":"In-memory state", "keywords":["cache"], "caveats":[]}))
    assert result.inputs == ("self: BaseDriver instance", "key: Cache key", "value: Value to store")
    assert result.outputs == ("No explicit return; None",)


@pytest.mark.parametrize("fence", ["```json", "```"])
def test_parse_accepts_single_fenced_json_object(fence):
    text = '{"summary":"s","purpose":"p","inputs":[],"outputs":[],"side_effects":[],"automation_context":"c","keywords":[],"caveats":[]}'
    assert parse_enrichment(fence + "\n" + text + "\n```").summary == "s"


def test_reply_decoder_does_not_extract_json_from_arbitrary_prose():
    from enrichment.parsing import decode_reply
    import json
    with pytest.raises(json.JSONDecodeError):
        decode_reply('Here is some JSON: {"summary":"s"}')
