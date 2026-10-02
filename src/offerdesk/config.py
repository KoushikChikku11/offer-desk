"""Load YAML config, falling back to the committed .example files."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DEFAULT_DB = DATA_DIR / "offerdesk.db"


def _load(name: str) -> dict:
    real = CONFIG_DIR / f"{name}.yaml"
    example = CONFIG_DIR / f"{name}.example.yaml"
    path = real if real.exists() else example
    if not path.exists():
        return {}
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def settings() -> dict:
    return _load("settings")


def profile() -> dict:
    return _load("profile")


def firms() -> list[dict]:
    path = CONFIG_DIR / "firms.yaml"
    if not path.exists():
        return []
    with path.open() as fh:
        return (yaml.safe_load(fh) or {}).get("firms", [])


def save_firms(firm_list: list[dict]) -> None:
    """Rewrite firms.yaml, used by `probe` to record which slugs are live."""
    path = CONFIG_DIR / "firms.yaml"
    header = []
    if path.exists():
        for line in path.read_text().splitlines():
            if line.startswith("#") or not line.strip():
                header.append(line)
            else:
                break
    body = yaml.safe_dump({"firms": firm_list}, sort_keys=False, width=120)
    path.write_text("\n".join(header) + "\n" + body)
