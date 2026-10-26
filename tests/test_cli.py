from pathlib import Path

import pytest

from cli import main
from settings import configured_source, configured_threshold


def test_configured_source_uses_saved_library_directory(tmp_path):
    source = tmp_path / "alternate-libs"
    source.mkdir()
    (tmp_path / "data").mkdir(exist_ok=True); (tmp_path / "data" / "library-source.txt").write_text(str(source), encoding="utf-8")

    assert configured_source(tmp_path) == source


def test_configured_threshold_uses_configs_json_value(tmp_path):
    (tmp_path / "configs.json").write_text('{"confidence_threshold": 0.7}', encoding="utf-8")

    assert configured_threshold(tmp_path) == 0.7


def test_configured_threshold_rejects_values_outside_zero_to_one(tmp_path):
    (tmp_path / "configs.json").write_text('{"confidence_threshold": 1.1}', encoding="utf-8")

    with pytest.raises(ValueError, match="confidence_threshold"):
        configured_threshold(tmp_path)


def test_catalog_command_writes_catalog_and_reports_skipped_files(capsys, library_root, tmp_path):
    destination = tmp_path / "catalog.json"

    assert main(["catalog", "--no-copilot", "--source", str(library_root), "--catalog", str(destination)]) == 0
    assert destination.exists()
    assert "cataloged" in capsys.readouterr().out


def test_find_warns_when_catalog_is_stale(capsys, library_root, tmp_path):
    destination = tmp_path / "catalog.json"
    main(["catalog", "--no-copilot", "--source", str(library_root), "--catalog", str(destination)])
    (library_root / "drivers" / "sample.py").write_text("def changed():\n    pass\n")

    assert main(["find", "wait for ticket", "--catalog", str(destination)]) == 0
    assert "STALE CATALOG" in capsys.readouterr().out


def test_find_renders_internal_warning_and_source_location(capsys, library_root, tmp_path, monkeypatch):
    destination = tmp_path / "catalog.json"
    main(["catalog", "--no-copilot", "--source", str(library_root), "--catalog", str(destination)])

    class UnavailableRanker:
        def __init__(self, on_evaluation=None):
            self.on_evaluation = on_evaluation

        def rank(self, goal, candidates):
            raise RuntimeError("unavailable for deterministic CLI rendering test")

    monkeypatch.setattr("cli.ranker_factory", lambda root: UnavailableRanker)

    assert main(["find", "click locator", "--catalog", str(destination)]) == 0
    output = capsys.readouterr().out
    assert "INTERNAL" in output
    assert ".py:" in output


def test_find_streams_each_laya_decision_and_summarizes_hits(capsys, library_root, tmp_path, monkeypatch):
    destination = tmp_path / "catalog.json"
    main(["catalog", "--no-copilot", "--source", str(library_root), "--catalog", str(destination)])
    capsys.readouterr()

    class LowConfidenceRanker:
        def __init__(self, on_evaluation=None):
            self.on_evaluation = on_evaluation

        def rank(self, goal, candidates):
            self.on_evaluation(1, len(candidates), candidates[0], 0.36)
            self.on_evaluation(2, len(candidates), candidates[1], 0.60)
            self.on_evaluation(3, len(candidates), candidates[2], 0.80)
            return {
                "recommendations": [
                    {
                        "method": candidates[0].qualified_name,
                        "confidence": 0.36,
                        "reason": "Laya assessed this method as fitting the goal.",
                    },
                    {
                        "method": candidates[1].qualified_name,
                        "confidence": 0.60,
                        "reason": "Laya assessed this method as fitting the goal.",
                    },
                    {
                        "method": candidates[2].qualified_name,
                        "confidence": 0.80,
                        "reason": "Laya assessed this method as fitting the goal.",
                    }
                ]
            }

    monkeypatch.setattr("cli.ranker_factory", lambda root: LowConfidenceRanker)
    monkeypatch.setattr("cli.configured_threshold", lambda _: 0.5)

    assert main(["find", "click locator", "--catalog", str(destination)]) == 0
    output = capsys.readouterr().out
    assert "[(1/" in output
    assert "[(2/" in output
    assert "[(3/" in output
    assert "] 0.36 Decline" in output
    assert "] 0.60 Passed" in output
    assert "Summary: 2 passed / 3 assessed" in output
    assert "Hits:\n[" in output
    assert output.count("0.60 Passed") == 2
    hits = output[output.index("Hits:"):]
    assert hits.index("0.80 Passed") < hits.index("0.60 Passed")


def test_settings_fall_back_to_tracked_example_until_configs_json_exists(tmp_path):
    from settings import configured_threshold, configured_workers
    (tmp_path / "configs.json.example").write_text('{"confidence_threshold": 0.4, "catalog_workers": 2}', encoding="utf-8")
    assert configured_threshold(tmp_path) == 0.4 and configured_workers(tmp_path) == 2
    (tmp_path / "configs.json").write_text('{"confidence_threshold": 0.9}', encoding="utf-8")
    assert configured_threshold(tmp_path) == 0.9
