"""Project root, configs.json, .env and the local data/ folder: the one place settings and paths are defined."""

from __future__ import annotations

import json
import os
from pathlib import Path

# <repo>/src/settings.py -> <repo>
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Local state (catalog, saved library paths, Copilot browser profile); gitignored.
DATA_DIR = PROJECT_ROOT / "data"


def state_dir(project_root: Path) -> Path:
    return project_root / "data"


def configured_source(project_root: Path) -> Path:
    """Return the saved source directory, or the adjacent SBM default."""
    return configured_sources(project_root)[0]


def configured_sources(project_root: Path) -> tuple[Path, ...]:
    setting = state_dir(project_root) / "library-source.txt"
    if setting.is_file():
        value = setting.read_text(encoding="utf-8").strip()
        if value:
            return tuple(dict.fromkeys(Path(line.strip()).expanduser().resolve() for line in value.splitlines() if line.strip()))
    return (project_root.parent / "SBM-Selenium-Automation-1" / "libs",)


def read_config(project_root: Path) -> dict:
    """Return configs.json (your local, untracked settings), or the tracked configs.json.example until it exists."""
    for name in ("configs.json", "configs.json.example"):
        try:
            return json.loads((project_root / name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
    raise FileNotFoundError("configs.json")


def configured_threshold(project_root: Path) -> float:
    """Return the strict Laya pass threshold from the project configuration."""
    try:
        config = read_config(project_root)
        threshold = config["confidence_threshold"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as error:
        raise ValueError("configs.json must define confidence_threshold") from error
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        raise ValueError("confidence_threshold must be a number from 0 through 1")
    return float(threshold)


def configured_workers(project_root: Path) -> int:
    config = read_config(project_root)
    # `copilot_workers` is the old name of this setting.
    workers = config.get("catalog_workers", config.get("copilot_workers", 1))
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 5:
        raise ValueError("catalog_workers must be an integer from 1 through 5")
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
        config = read_config(project_root)
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
