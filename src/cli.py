"""Command-line interface for safe library discovery."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from core.catalog import load_catalog, save_catalog, stale_files
from core.scanner import scan_library
from core.sources import scan_sources, catalog_roots
from core.search import retrieve
from core.service import find_methods
from backends.factory import enrichment_factory, ranker_factory
from enrichment.pipeline import enrich_catalog
from settings import (DATA_DIR, PROJECT_ROOT, configured_sources,
                                     configured_threshold, configured_workers)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="library-finder")
    commands = parser.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("catalog")
    catalog.add_argument("--source", type=Path, nargs="+", default=None)
    catalog.add_argument("--catalog", type=Path, default=DATA_DIR / "catalog.json")
    catalog.add_argument("--strict", action="store_true")
    catalog.add_argument("--no-enrichment", action="store_true")
    find = commands.add_parser("find")
    find.add_argument("goal")
    find.add_argument("--catalog", type=Path, default=DATA_DIR / "catalog.json")
    show = commands.add_parser("show")
    show.add_argument("method")
    show.add_argument("--catalog", type=Path, default=DATA_DIR / "catalog.json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "catalog":
        roots = args.source or configured_sources(PROJECT_ROOT)
        scanned = scan_sources(roots)
        previous = None
        if args.catalog.is_file():
            try:
                candidate = load_catalog(args.catalog)
                previous = candidate if len(catalog_roots(candidate)) > 1 or catalog_roots(candidate) == catalog_roots(scanned) else None
            except Exception:
                previous = None
        if args.no_enrichment:
            catalog = scanned
            summary = None
        else:
            root = PROJECT_ROOT
            factory = enrichment_factory(root)
            print("Cataloging sends changed source snippets to the configured MiniCPM service.")
            workers = configured_workers(root)
            if workers > 1:
                from enrichment.parallel import enrich_parallel
                catalog, summary = enrich_parallel(scanned, previous, factory, workers,
                    on_checkpoint=lambda snapshot: save_catalog(snapshot, args.catalog))
                save_catalog(catalog, args.catalog)
                print(f"cataloged {len(catalog.methods)} methods; reused {summary.reused}; enriched {summary.enriched}; failed {summary.failed}; pending {summary.pending}")
                return 1 if args.strict and (catalog.errors or summary.failed or summary.pending) else 0
            client = factory()
            try:
                catalog, summary = enrich_catalog(scanned, previous, client,
                    on_checkpoint=lambda snapshot: save_catalog(snapshot, args.catalog))
            finally:
                client.close()
        save_catalog(catalog, args.catalog)
        print(f"cataloged {len(catalog.methods)} methods; skipped {len(catalog.errors)} files")
        if summary:
            print(f"enrichment: reused {summary.reused}; enriched {summary.enriched}; failed {summary.failed}; pending {summary.pending}")
            if summary.pending:
                print("Catalog build interrupted; successful results saved. Rebuild to retry pending methods.")
        return 1 if args.strict and (catalog.errors or (summary and summary.failed)) else 0
    threshold = configured_threshold(PROJECT_ROOT)
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

    result = find_methods(args.goal, catalog, ranker_factory(PROJECT_ROOT)(on_evaluation=print_laya_evaluation))
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
