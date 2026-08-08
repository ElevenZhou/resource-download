"""HLS download via ffmpeg with concurrency."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .api import GoodShortAPIError, GoodShortClient
from .store import EpisodeRow, Library, safe_name


@dataclass
class DownloadResult:
    chapter_id: str
    book_id: str
    ok: bool
    path: Optional[Path] = None
    error: Optional[str] = None
    skipped: bool = False


class FFmpegDownloader:
    def __init__(
        self,
        client: GoodShortClient,
        library: Library,
        download_root: Path,
        workers: int = 3,
        ffmpeg_bin: str = "ffmpeg",
        timeout: int = 600,
        free_only: bool = True,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.client = client
        self.library = library
        self.download_root = Path(download_root)
        self.workers = max(1, workers)
        self.ffmpeg_bin = ffmpeg_bin
        self.timeout = timeout
        self.free_only = free_only
        self.on_progress = on_progress or (lambda msg: None)
        self._lock = threading.Lock()

        if not shutil.which(self.ffmpeg_bin):
            raise RuntimeError(
                f"ffmpeg not found ({self.ffmpeg_bin}). Install: brew install ffmpeg"
            )

    def output_path(self, book_id: str, episode: EpisodeRow) -> Path:
        drama = self.library.get_drama(book_id)
        dir_name = drama.dir_name if drama else book_id
        folder = self.download_root / dir_name
        ep_no = episode.ep_index or 0
        name = safe_name(episode.chapter_name, 40)
        filename = f"EP{ep_no:03d}_{name}_{episode.chapter_id}.mp4"
        return folder / filename

    def download_one(self, episode: EpisodeRow) -> DownloadResult:
        out = self.output_path(episode.book_id, episode)
        out.parent.mkdir(parents=True, exist_ok=True)

        if out.exists() and out.stat().st_size > 10_000:
            self.library.mark_episode(
                episode.chapter_id,
                "done",
                file_path=str(out),
                file_size=out.stat().st_size,
            )
            return DownloadResult(
                chapter_id=episode.chapter_id,
                book_id=episode.book_id,
                ok=True,
                path=out,
                skipped=True,
            )

        if self.free_only and episode.price > 0 and not episode.m3u8_path:
            self.library.mark_episode(episode.chapter_id, "locked", error="paid episode")
            return DownloadResult(
                chapter_id=episode.chapter_id,
                book_id=episode.book_id,
                ok=False,
                error="locked/paid",
            )

        self.library.mark_episode(episode.chapter_id, "downloading", inc_attempt=True)
        self.on_progress(
            f"↓ {episode.book_id} EP{episode.ep_index:03d} {episode.chapter_name}"
        )

        try:
            m3u8 = self.client.resolve_m3u8(
                episode.book_id, episode.chapter_id, cached=episode.m3u8_path
            )
        except GoodShortAPIError as exc:
            status = "locked" if "no m3u8" in str(exc).lower() else "failed"
            self.library.mark_episode(episode.chapter_id, status, error=str(exc))
            return DownloadResult(
                chapter_id=episode.chapter_id,
                book_id=episode.book_id,
                ok=False,
                error=str(exc),
            )

        tmp = out.with_suffix(".part.mp4")
        if tmp.exists():
            tmp.unlink()

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-loglevel",
            "error",
            "-stats",
            "-user_agent",
            self.client.user_agent,
            "-headers",
            f"Referer: {self.client.base_url}/\r\nOrigin: {self.client.base_url}\r\n",
            "-i",
            m3u8,
            "-c",
            "copy",
            "-bsf:a",
            "aac_adtstoasc",
            str(tmp),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1000:
                err = (proc.stderr or proc.stdout or "ffmpeg failed").strip()
                err = err[-500:]
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                self.library.mark_episode(
                    episode.chapter_id, "failed", error=err, m3u8_path=m3u8
                )
                return DownloadResult(
                    chapter_id=episode.chapter_id,
                    book_id=episode.book_id,
                    ok=False,
                    error=err,
                )

            tmp.replace(out)
            size = out.stat().st_size
            self.library.mark_episode(
                episode.chapter_id,
                "done",
                file_path=str(out),
                file_size=size,
                m3u8_path=m3u8,
            )
            self.on_progress(
                f"✓ {episode.book_id} EP{episode.ep_index:03d} "
                f"({size / 1024 / 1024:.1f} MB) -> {out.name}"
            )
            return DownloadResult(
                chapter_id=episode.chapter_id,
                book_id=episode.book_id,
                ok=True,
                path=out,
            )
        except subprocess.TimeoutExpired:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            err = f"timeout after {self.timeout}s"
            self.library.mark_episode(episode.chapter_id, "failed", error=err)
            return DownloadResult(
                chapter_id=episode.chapter_id,
                book_id=episode.book_id,
                ok=False,
                error=err,
            )
        except Exception as exc:  # noqa: BLE001
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            self.library.mark_episode(episode.chapter_id, "failed", error=str(exc))
            return DownloadResult(
                chapter_id=episode.chapter_id,
                book_id=episode.book_id,
                ok=False,
                error=str(exc),
            )

    def run_batch(self, episodes: list[EpisodeRow]) -> list[DownloadResult]:
        if not episodes:
            return []
        results: list[DownloadResult] = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self.download_one, ep): ep for ep in episodes}
            for fut in as_completed(futures):
                results.append(fut.result())
        return results
