import json
from dataclasses import replace
from library_finder.scanner import scan_library
from library_finder.enrichment import enrich_catalog
from library_finder.copilot import parse_batch, CopilotResponseError, build_batch_prompt
from library_finder.models import CopilotEnrichment


def test_batches_five_methods_and_checkpoints_each_result(library_root):
    scanned = scan_library(library_root)
    sample = scanned.methods[0]
    scanned = replace(scanned, methods=tuple(replace(sample, qualified_name=f"libs.method{i}") for i in range(12)))
    batches, saved = [], []
    class Client:
        def enrich_batch(self, methods):
            if batches:
                assert sum(m.enrichment_status == "success" for m in saved[-1].methods) == sum(len(batch) for batch in batches)
            batches.append(methods)
            return {m.qualified_name: CopilotEnrichment("s", "p", (), (), (), "c", (), ()) for m in methods}
    result, summary = enrich_catalog(scanned, None, Client(), on_checkpoint=saved.append)
    assert list(map(len, batches)) == [5, 5, 2]
    assert summary.enriched == 12
    assert len(saved) == 12
    assert all(m.enrichment_status == "success" for m in result.methods)


def test_batch_parser_maps_by_identity_and_preserves_partial_results(library_root):
    methods = scan_library(library_root).methods[:2]
    payload = {methods[1].qualified_name: {"summary":"s", "purpose":"p", "inputs":[], "outputs":[], "side_effects":[], "automation_context":"c", "keywords":[], "caveats":[]}}
    result = parse_batch(json.dumps(payload), methods)
    assert isinstance(result[methods[0].qualified_name], CopilotResponseError)
    assert result[methods[1].qualified_name].summary == "s"
    assert all(m.qualified_name in build_batch_prompt(methods) for m in methods)


def test_extra_unknown_identity_does_not_discard_valid_result(library_root):
    methods = scan_library(library_root).methods[:1]
    value = {"summary":"s", "purpose":"p", "inputs":[], "outputs":[], "side_effects":[], "automation_context":"c", "keywords":[], "caveats":[]}
    result = parse_batch(json.dumps({methods[0].qualified_name:value, "unexpected":value}), methods)
    assert result[methods[0].qualified_name].summary == "s"
    assert "unexpected" not in result


def test_wrapped_result_list_uses_exact_qualified_names(library_root):
    methods = scan_library(library_root).methods[:2]
    value = {"summary":"s", "purpose":"p", "inputs":[], "outputs":[], "side_effects":[], "automation_context":"c", "keywords":[], "caveats":[]}
    payload = {"results":[{"qualified_name":methods[1].qualified_name, "enrichment":value}, {"qualified_name":"invented", "enrichment":value}]}
    result = parse_batch(json.dumps(payload), methods)
    assert result[methods[1].qualified_name].summary == "s"
    assert isinstance(result[methods[0].qualified_name], CopilotResponseError)
