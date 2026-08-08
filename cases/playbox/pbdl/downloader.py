"""HTTP concurrent downloader for Playbox assets."""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from .api import DEFAULT_SITE, DEFAULT_UA, PlayboxAPIError, PlayboxClient
from .store import AssetRow, Library

KIND_EXT = {
    "character": ".webp",
    "cover": ".png",
    "video": ".mp4",
}


def _default_ext(kind: str) -> str:
    if kind.startswith("character"):
        return ".webp"
    return KIND_EXT.get(kind, ".bin")


@dataclass
class DownloadResult:
    asset_id: str
    item_id: str
    kind: str
    ok: bool
    path: Optional[Path] = None
    error: Optional[str] = None
    skipped: bool = False


class MediaDownloader:
    def __init__(
        self,
        client: PlayboxClient,
        library: Library,
        download_root: Path,
        workers: int = 4,
        timeout: int = 120,
        min_bytes: int = 512,
        refresh_on_fail: bool = True,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.client = client
        self.library = library
        self.download_root = Path(download_root)
        self.workers = max(1, workers)
        self.timeout = timeout
        self.min_bytes = min_bytes
        self.refresh_on_fail = refresh_on_fail
        self.on_progress = on_progress or (lambda _m: None)
        self._lock = threading.Lock()
        self._refreshed: set[str] = set()

    def run_batch(self, assets: list[AssetRow]) -> list[DownloadResult]:
        if not assets:
            return []
        results: list[DownloadResult] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(self._download_one, a): a for a in assets}
            for fut in as_completed(futs):
                results.append(fut.result())
        return results

    def _download_one(self, asset: AssetRow) -> DownloadResult:
        self.library.mark_downloading(asset.asset_id)
        item = self.library.get_item(asset.item_id)
        if not item:
            err = "item missing in library"
            self.library.mark_failed(asset.asset_id, err)
            return DownloadResult(asset.asset_id, asset.item_id, asset.kind, False, error=err)

        out_dir = self.download_root / item.dir_name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Candidate URLs: stored → detail refresh → character fallback (resized)
        candidates: list[str] = []
        if asset.url:
            candidates.append(asset.url)

        last_err = "download failed"
        tried: set[str] = set()
        for url in candidates:
            if not url or url in tried:
                continue
            tried.add(url)
            try:
                dest = self._resolve_dest(out_dir, asset.kind, url)
                if dest.exists() and dest.stat().st_size >= self.min_bytes:
                    self.library.mark_done(asset.asset_id, str(dest), dest.stat().st_size)
                    try:
                        self.library.write_meta_json(asset.item_id, self.download_root)
                    except Exception:
                        pass
                    self.on_progress(
                        f"  · skip existing {asset.kind} {item.name[:40]} -> {dest.name}"
                    )
                    return DownloadResult(
                        asset.asset_id, asset.item_id, asset.kind, True, path=dest, skipped=True
                    )

                part = dest.with_suffix(dest.suffix + ".part")
                size = self._http_download(url, part)
                if size < self.min_bytes:
                    part.unlink(missing_ok=True)
                    raise RuntimeError(f"file too small ({size} bytes)")
                part.replace(dest)
                # persist working url
                self.library.update_asset_url(asset.asset_id, url)
                self.library.mark_done(asset.asset_id, str(dest), size)
                try:
                    self.library.write_meta_json(asset.item_id, self.download_root)
                except Exception:
                    pass
                self.on_progress(
                    f"  ✓ {asset.kind:12} {item.username}/{item.name[:36]}  {size}B  {dest.name}"
                )
                return DownloadResult(asset.asset_id, asset.item_id, asset.kind, True, path=dest)
            except Exception as exc:
                last_err = str(exc)
                # On failure, queue more candidates once
                if self.refresh_on_fail:
                    for extra in self._extra_urls(asset):
                        if extra not in tried:
                            candidates.append(extra)

        self.library.mark_failed(asset.asset_id, last_err)
        self.on_progress(f"  ✗ {asset.kind:12} {item.name[:36]}  {last_err}")
        return DownloadResult(
            asset.asset_id, asset.item_id, asset.kind, False, error=last_err
        )

    def _extra_urls(self, asset: AssetRow) -> list[str]:
        """Detail refresh + character resized fallbacks."""
        out: list[str] = []
        with self._lock:
            need_refresh = asset.item_id not in self._refreshed
            if need_refresh:
                self._refreshed.add(asset.item_id)
        fresh = None
        if need_refresh:
            try:
                fresh = self.client.collection_detail(asset.item_id)
                self.library.refresh_item_urls(fresh)
            except PlayboxAPIError:
                fresh = None
        if fresh is None:
            # re-read media from library raw is unavailable; try detail always soft
            try:
                fresh = self.client.collection_detail(asset.item_id)
            except PlayboxAPIError:
                return out
        media = fresh.media_map()
        if asset.kind in media:
            out.append(media[asset.kind])
        fb = fresh.fallback_for_kind(asset.kind)
        if fb:
            out.append(fb)
        # also try raw slot primary if download_url preferred fallback
        if asset.kind.startswith("character") and fresh.character_images:
            idx = 0 if asset.kind == "character" else int(asset.kind.split("_")[1]) - 1
            if 0 <= idx < len(fresh.character_images):
                ch = fresh.character_images[idx]
                for u in (ch.url, ch.fallback_url, ch.download_url):
                    if u:
                        out.append(u)
        return out

    def _resolve_dest(self, out_dir: Path, kind: str, url: str) -> Path:
        ext = self._guess_ext(kind, url)
        return out_dir / f"{kind}{ext}"

    @staticmethod
    def _guess_ext(kind: str, url: str) -> str:
        path = urlparse(url).path
        suffix = Path(path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov"}:
            return suffix
        return _default_ext(kind)

    def _http_download(self, url: str, dest: Path) -> int:
        # Some CDNs (e.g. DigitalOcean Spaces) reject hotlink Referer.
        header_variants = [
            {
                "User-Agent": DEFAULT_UA,
                "Accept": "*/*",
                "Referer": f"{DEFAULT_SITE}/",
                "Origin": DEFAULT_SITE,
            },
            {
                "User-Agent": DEFAULT_UA,
                "Accept": "*/*",
            },
        ]
        last_err: Optional[Exception] = None
        data: Optional[bytes] = None
        for headers in header_variants:
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                break
            except urllib.error.HTTPError as e:
                last_err = RuntimeError(f"HTTP {e.code}")
                if e.code not in (401, 403):
                    raise last_err from e
                continue
            except urllib.error.URLError as e:
                raise RuntimeError(f"network: {e.reason}") from e
        if data is None:
            raise last_err or RuntimeError("download failed")

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return len(data)
