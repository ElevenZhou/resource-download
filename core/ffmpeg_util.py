"""ffmpeg detection helpers."""

from __future__ import annotations

import shutil
from typing import Optional


def find_ffmpeg(binary: str = "ffmpeg") -> Optional[str]:
    return shutil.which(binary)


def require_ffmpeg(binary: str = "ffmpeg") -> str:
    path = find_ffmpeg(binary)
    if not path:
        raise RuntimeError(
            f"{binary} not found in PATH. Install e.g. `brew install ffmpeg`."
        )
    return path
