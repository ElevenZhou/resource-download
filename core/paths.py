"""Path and filename helpers shared across cases."""

from __future__ import annotations

import re
from pathlib import Path


def repo_root() -> Path:
    """Repository root (parent of `core/`)."""
    return Path(__file__).resolve().parent.parent


def cases_root() -> Path:
    return repo_root() / "cases"


def case_dir(name: str) -> Path:
    return cases_root() / name


def safe_name(name: str, max_len: int = 80) -> str:
    """Filesystem-safe directory / file base name."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(". ")
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:max_len]


def ensure_dir(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path
