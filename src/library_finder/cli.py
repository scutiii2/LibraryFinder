"""Command-line interface for safe library discovery."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from library_finder.catalog import load_catalog, save_catalog, stale_files
from library_finder.scanner import scan_library
from library_finder.sources import scan_sources, catalog_roots
from library_finder.search import retrieve
from library_finder.laya_ranker import LayaRanker
from library_finder.service import find_methods
from library_finder.enrichment import enrich_catalog
from library_finder.copilot import CopilotClient


def _default_source() -> Path:
    return configured_source(Path(__file__).resolve().parents[2])


def configured_source(project_root: Path) -> Path:
    """Return the saved source directory, or the adjacent SBM default."""
    return configured_sources(project_root)[0]


def configured_sources(project_root: Path) -> tuple[Path, ...]:
    setting = project_root / "library-source.txt"
    if setting.is_file():
        value = setting.read_text(encoding="utf-8").strip()
        if value:
            return tuple(dict.fromkeys(Path(line.strip()).expanduser().resolve() for line in value.splitlines() if line.strip()))
    return (project_root.parent / "SBM-Selenium-Automation-1" / "libs",)


def configured_threshold(project_root: Path) -> float:
    """Return the strict Laya pass threshold from the project configuration."""
    config_path = project_root / "configs.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        threshold = config["confidence_threshold"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as error:
        raise ValueError("configs.json must define confidence_threshold") from error
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        raise ValueError("confidence_threshold must be a number from 0 through 1")
    return float(threshold)


def configured_workers(project_root: Path) -> int:
    config = json.loads((project_root / "configs.json").read_text(encoding="utf-8"))
    workers = config.get("copilot_workers", 1)
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 5:
        raise ValueError("copilot_workers must be an integer from 1 through 5")
    return workers


def load_env(project_root: Path) -> None:
    """Load KEY=VALUE lines from <project_root>/.env into os.environ; variables already set win."""
    try:
        raw = (project_root / ".env").read_bytes()
    except FileNotFoundError:
        return
    # PowerShell's `>` writes UTF-16 with a BOM; accept that and UTF-8 with or without a BOM.
    lines = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig").splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip().strip("\"'")
        if key and value: os.environ.setdefault(key, value)


def configured_backends(project_root: Path) -> dict:
    """Backend choices: environment (.env) over configs.json over local defaults (Copilot browser, in-process Laya)."""
    load_env(project_root)
    try:
        config = json.loads((project_root / "configs.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        config = {}
    settings = {"enrichment_backend": config.get("enrichment_backend", "copilot"),
                "minicpm_url": os.environ.get("MINICPM_URL") or config.get("minicpm_url", "http://127.0.0.1:9130"),
                "laya_backend": config.get("laya_backend", "local"),
                "laya_url": os.environ.get("LAYA_URL") or config.get("laya_url", "http://127.0.0.1:9120")}
    if settings["enrichment_backend"] not in ("copilot", "minicpm"):
        raise ValueError("enrichment_backend must be copilot or minicpm")
    if settings["laya_backend"] not in ("local", "service"):
        raise ValueError("laya_backend must be local or service")
    return settings


def enrichment_factory(project_root: Path):
    """Return a zero-argument callable that builds the configured catalog client."""
    settings = configured_backends(project_root)
    if settings["enrichment_backend"] == "minicpm":
        from library_finder.minicpm import MiniCPMClient
        return lambda: MiniCPMClient(settings["minicpm_url"])
    return CopilotClient


def ranker_factory(project_root: Path):
    """Return the Laya ranker class or factory (accepts on_evaluation=) for the configured backend."""
    settings = configured_backends(project_root)
    if settings["laya_backend"] == "service":
        from library_finder.laya_ranker import LayaServiceRanker
        return lambda on_evaluation=None: LayaServiceRanker(settings["laya_url"], on_evaluation=on_evaluation)
    return LayaRanker


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="library-finder")
    commands = parser.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("catalog")
    catalog.add_argument("--source", type=Path, nargs="+", default=None)
    catalog.add_argument("--catalog", type=Path, default=Path("catalog.json"))
    catalog.add_argument("--strict", action="store_true")
    catalog.add_argument("--no-copilot", action="store_true")
    find = commands.add_parser("find")
    find.add_argument("goal")
    find.add_argument("--catalog", type=Path, default=Path("catalog.json"))
    show = commands.add_parser("show")
    show.add_argument("method")
    show.add_argument("--catalog", type=Path, default=Path("catalog.json"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "catalog":
        roots = args.source or configured_sources(Path(__file__).resolve().parents[2])
        scanned = scan_sources(roots)
        previous = None
        if args.catalog.is_file():
            try:
                candidate = load_catalog(args.catalog)
                previous = candidate if len(catalog_roots(candidate)) > 1 or catalog_roots(candidate) == catalog_roots(scanned) else None
            except Exception:
                previous = None
        if args.no_copilot:
            catalog = scanned
            summary = None
        else:
            root = Path(__file__).resolve().parents[2]
            factory = enrichment_factory(root)
            if configured_backends(root)["enrichment_backend"] == "minicpm":
                print("Cataloging sends changed source snippets to the configured MiniCPM service.")
            else:
                print("Cataloging sends changed source snippets to your signed-in Microsoft 365 Copilot account.")
            workers = configured_workers(root)
            if workers > 1:
                from library_finder.parallel_catalog import enrich_parallel
                catalog, summary, deleted = enrich_parallel(scanned, previous, factory, workers,
                    on_checkpoint=lambda snapshot: save_catalog(snapshot, args.catalog))
                save_catalog(catalog, args.catalog)
                print(f"cataloged {len(catalog.methods)} methods; reused {summary.reused}; enriched {summary.enriched}; failed {summary.failed}; pending {summary.pending}")
                if not deleted: print("Copilot conversation cleanup was not confirmed for every worker.")
                return 1 if args.strict and (catalog.errors or summary.failed or summary.pending) else 0
            client = factory()
            try:
                catalog, summary = enrich_catalog(scanned, previous, client,
                    on_checkpoint=lambda snapshot: save_catalog(snapshot, args.catalog))
            finally:
                deleted = client.close()
                if client.conversation_url and not deleted:
                    print("WARNING: the dedicated Copilot conversation could not be confirmed deleted.")
        save_catalog(catalog, args.catalog)
        print(f"cataloged {len(catalog.methods)} methods; skipped {len(catalog.errors)} files")
        if summary:
            print(f"enrichment: reused {summary.reused}; enriched {summary.enriched}; failed {summary.failed}; pending {summary.pending}")
            if summary.pending:
                print("Catalog build interrupted; successful results saved. Copilot window retained for inspection.")
        return 1 if args.strict and (catalog.errors or (summary and summary.failed)) else 0
    threshold = configured_threshold(Path(__file__).resolve().parents[2])
    catalog = load_catalog(args.catalog)
    stale = stale_files(catalog)
    if stale:
        print(f"STALE CATALOG: {len(stale)} source files changed; run catalog to refresh.")
    if args.command == "show":
        record = catalog.method(args.method)
        if record is None:
            print(f"No catalog method named {args.method}")
            return 1
        print(f"{record.qualified_name} — {record.file}:{record.line}")
        return 0
    def print_laya_evaluation(current: int, total: int, record, confidence: float) -> None:
        decision = "Passed" if confidence > threshold else "Decline"
        print(f"[({current}/{total}) {record.qualified_name} — {record.file}:{record.line}] {confidence:.2f} {decision}")

    result = find_methods(args.goal, catalog, ranker_factory(Path(__file__).resolve().parents[2])(on_evaluation=print_laya_evaluation))
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    if result.used_laya:
        hits = sorted(
            (recommendation for recommendation in result.recommendations if recommendation.confidence > threshold),
            key=lambda recommendation: recommendation.confidence,
            reverse=True,
        )
        print(f"Summary: {len(hits)} passed / {len(result.recommendations)} assessed")
        if not hits:
            print("Hits: none")
            return 0
        print("Hits:")
        for recommendation in hits:
            record = recommendation.method
            print(f"[{record.qualified_name} — {record.file}:{record.line}] {recommendation.confidence:.2f} Passed")
        return 0
    for recommendation in result.recommendations:
        record = recommendation.method
        internal = " INTERNAL" if record.visibility == "internal" else ""
        effects = f" effects={','.join(record.effects)}" if record.effects else ""
        print(f"{record.qualified_name}{internal} — {record.file}:{record.line}{effects} — {recommendation.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
