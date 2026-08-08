"""Playbox public API client (explore / collection detail)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

DEFAULT_API_BASE = "https://api.playbox.com/api"
DEFAULT_SITE = "https://www.playbox.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class PlayboxAPIError(RuntimeError):
    pass


@dataclass
class CharacterImage:
    """One person/source still for a collection."""

    url: str
    fallback_url: str = ""  # e.g. resizedImage when full-res is private CDN

    @property
    def download_url(self) -> str:
        """Prefer publicly reachable CDN over private train storage."""
        primary = (self.url or "").strip()
        fb = (self.fallback_url or "").strip()
        if primary and _looks_private_cdn(primary) and fb and not _looks_private_cdn(fb):
            return fb
        return primary or fb


@dataclass
class CollectionItem:
    """One Explore / collection card."""

    item_id: str
    name: str
    username: str
    user_id: str = ""
    character_image: str = ""  # first character (compat)
    character_images: list[CharacterImage] = field(default_factory=list)
    cover_image: str = ""  # output.video.posterUrl — 封面
    video_url: str = ""  # output.video.url — 视频
    video_compressed: str = ""
    # --- text / taxonomy ---
    tags: list[str] = field(default_factory=list)  # e.g. VIP, Free Trial, BLEND
    keywords: list[str] = field(default_factory=list)  # content labels
    categories: list[str] = field(default_factory=list)  # Trending / New / ...
    model_type: str = ""  # GENERATE_VIDEO / BLEND_MODEL / ...
    model_id: str = ""  # template/model id
    model_name: str = ""  # internal template code, e.g. PB_USER_...
    template_name: str = ""  # human template title (feed often has this)
    template_creator: str = ""  # template author username
    description: str = ""
    custom_prompt: str = ""
    status: str = ""
    is_public: bool = True
    page_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def text_meta(self) -> dict[str, Any]:
        """Serializable textual metadata for meta.json / export."""
        return {
            "item_id": self.item_id,
            "name": self.name,
            "username": self.username,
            "user_id": self.user_id,
            "template_name": self.template_name or self.model_name or self.name,
            "template_creator": self.template_creator or self.username,
            "model_id": self.model_id,
            "model_name": self.model_name,
            "model_type": self.model_type,
            "description": self.description,
            "custom_prompt": self.custom_prompt,
            "tags": list(self.tags),
            "keywords": list(self.keywords),
            "categories": list(self.categories),
            "page_url": self.page_url,
            "is_public": self.is_public,
            "status": self.status,
        }

    @property
    def has_video(self) -> bool:
        return bool(self.video_url or self.video_compressed)

    @property
    def best_video(self) -> str:
        return self.video_url or self.video_compressed

    def media_map(self) -> dict[str, str]:
        """kind -> url for downloadable assets present.

        Multiple person stills become character, character_2, character_3, ...
        """
        out: dict[str, str] = {}
        chars = self.character_images or (
            [CharacterImage(url=self.character_image)] if self.character_image else []
        )
        for i, ch in enumerate(chars):
            url = ch.download_url
            if not url:
                continue
            kind = "character" if i == 0 else f"character_{i + 1}"
            out[kind] = url
        if self.cover_image:
            out["cover"] = self.cover_image
        if self.best_video:
            out["video"] = self.best_video
        return out

    def fallback_for_kind(self, kind: str) -> str:
        """Return alternate URL for a character* asset (resized / other CDN)."""
        if not kind.startswith("character"):
            return ""
        chars = self.character_images or []
        if kind == "character":
            idx = 0
        elif kind.startswith("character_"):
            try:
                idx = int(kind.split("_", 1)[1]) - 1
            except ValueError:
                return ""
        else:
            return ""
        if idx < 0 or idx >= len(chars):
            return ""
        ch = chars[idx]
        # if download_url already chose fallback, offer the other side
        chosen = ch.download_url
        for cand in (ch.url, ch.fallback_url):
            if cand and cand != chosen:
                return cand
        return ch.fallback_url if ch.fallback_url != chosen else ""


def _looks_private_cdn(url: str) -> bool:
    u = url.lower()
    return "digitaloceanspaces.com" in u or "/train/" in u


def _nested_url(obj: Any, *keys: str) -> str:
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(k)
    if isinstance(cur, str):
        return cur.strip()
    return ""


def _url_key(url: str) -> str:
    """Dedupe key: strip query string."""
    return (url or "").split("?", 1)[0].strip()


def _slot_from_image_obj(img: Any) -> Optional[CharacterImage]:
    if isinstance(img, str) and img.startswith("http"):
        return CharacterImage(url=img.strip())
    if not isinstance(img, dict):
        return None
    primary = str(img.get("url") or "").strip()
    fallback = ""
    resized = img.get("resizedImage")
    if isinstance(resized, dict):
        fallback = str(resized.get("url") or "").strip()
    if not primary and isinstance(img.get("sourceUrl"), str):
        primary = img["sourceUrl"].strip()
    if not primary and fallback:
        primary, fallback = fallback, ""
    if not primary:
        return None
    if fallback and _url_key(fallback) == _url_key(primary):
        fallback = ""
    return CharacterImage(url=primary, fallback_url=fallback)


def extract_character_images(inp: Any) -> list[CharacterImage]:
    """Collect all person/source stills from input payload (1..N)."""
    if not isinstance(inp, dict):
        return []

    slots: list[CharacterImage] = []

    def add(img: Any) -> None:
        slot = _slot_from_image_obj(img)
        if slot:
            slots.append(slot)

    # Single / multi fields used by generate form
    if "image" in inp:
        add(inp.get("image"))
    for key in ("image2", "image3", "image4", "image5", "image6"):
        if key in inp and inp.get(key) is not None:
            add(inp.get(key))

    for key in (
        "imageUrl",
        "imageUrl2",
        "imageUrl3",
        "image2Url",
        "image3Url",
        "imageUrl1",
    ):
        if key in inp and inp.get(key) is not None:
            add(inp.get(key))

    for key in ("images", "inputImages", "imagesUrl", "inputImagesUrl"):
        arr = inp.get(key)
        if isinstance(arr, list):
            for el in arr:
                add(el)

    # Dedupe by path (ignore token query); keep first occurrence order
    seen: set[str] = set()
    unique: list[CharacterImage] = []
    for s in slots:
        key = _url_key(s.download_url or s.url)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(s)
    return unique


def parse_collection(raw: dict[str, Any], site: str = DEFAULT_SITE) -> CollectionItem:
    item_id = str(raw.get("_id") or raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip() or item_id or "untitled"
    username = str(raw.get("username") or "").strip()
    user_id = str(raw.get("user") or "").strip()

    inp = raw.get("input") or {}
    out = raw.get("output") or {}
    characters = extract_character_images(inp)
    character = characters[0].download_url if characters else ""

    video = out.get("video") if isinstance(out.get("video"), dict) else {}
    cover = str(video.get("posterUrl") or "").strip()
    video_url = str(video.get("url") or "").strip()
    video_compressed = str(video.get("compressedUrl") or "").strip()

    # output still (e.g. Nudify / edit) is not a "character" but useful image —
    # only add as extra character if no video and distinct from inputs
    out_img = _slot_from_image_obj(out.get("image") if isinstance(out.get("image"), dict) else None)
    if out_img and not video_url and not video_compressed:
        ok = _url_key(out_img.download_url)
        if ok and ok not in {_url_key(c.download_url) for c in characters}:
            characters.append(out_img)

    tags: list[str] = []
    for t in raw.get("tags") or []:
        if isinstance(t, dict) and t.get("name"):
            tags.append(str(t["name"]))
        elif isinstance(t, str):
            tags.append(t)

    keywords = [str(k) for k in (raw.get("keywords") or []) if k]
    categories: list[str] = []
    for c in raw.get("categories") or []:
        if isinstance(c, dict) and c.get("name"):
            categories.append(str(c["name"]))
        elif isinstance(c, str):
            categories.append(c)

    page_url = f"{site.rstrip('/')}/collection/{item_id}" if item_id else ""
    model_id = str(raw.get("model") or "").strip()
    model_name = str(raw.get("modelName") or "").strip()
    template_name = str(
        raw.get("templateName") or raw.get("modelName") or ""
    ).strip()
    template_creator = str(
        raw.get("templateCreatorUsername") or raw.get("templateCreator") or ""
    ).strip()
    description = str(raw.get("description") or "").strip()
    custom_prompt = str(raw.get("customPrompt") or "").strip()

    return CollectionItem(
        item_id=item_id,
        name=name,
        username=username,
        user_id=user_id,
        character_image=character,
        character_images=characters,
        cover_image=cover,
        video_url=video_url,
        video_compressed=video_compressed,
        tags=tags,
        keywords=keywords,
        categories=categories,
        model_type=str(raw.get("modelType") or ""),
        model_id=model_id,
        model_name=model_name,
        template_name=template_name,
        template_creator=template_creator,
        description=description,
        custom_prompt=custom_prompt,
        status=str(raw.get("status") or ""),
        is_public=bool(raw.get("isPublic", True)),
        page_url=page_url,
        raw=raw,
    )


class PlayboxClient:
    def __init__(
        self,
        api_base: str = DEFAULT_API_BASE,
        site: str = DEFAULT_SITE,
        user_agent: str = DEFAULT_UA,
        timeout: int = 30,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.site = site.rstrip("/")
        self.user_agent = user_agent
        self.timeout = timeout

    def _request_json(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        url = f"{self.api_base}{path}"
        if params:
            q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""})
            if q:
                url = f"{url}?{q}" if "?" not in url else f"{url}&{q}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "Origin": self.site,
                "Referer": f"{self.site}/explore/",
                "X-MY-CSRF-PROTECTION": "5",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if resp.status != 200:
                    raise PlayboxAPIError(f"HTTP {resp.status} for {url}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise PlayboxAPIError(f"HTTP {e.code} for {url}: {detail}") from e
        except urllib.error.URLError as e:
            raise PlayboxAPIError(f"network error for {url}: {e}") from e

        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise PlayboxAPIError(f"invalid JSON from {url}") from e

    def explore(
        self,
        page: int = 1,
        category_id: str = "",
        search: str = "",
        keyword: str = "",
    ) -> list[CollectionItem]:
        """Fetch one Explore page (public, no login)."""
        path = f"/model/explore/{category_id}" if category_id else "/model/explore/"
        # API expects trailing structure used by frontend: /model/explore/?page=
        if not path.endswith("/"):
            path = path + "/"
        params: dict[str, Any] = {"page": page}
        if search:
            params["search"] = search
        if keyword and keyword != "All":
            params["keyword"] = "Disturbing" if keyword == "Bizarre" else keyword

        payload = self._request_json(path, params)
        data = payload.get("data", payload)
        if isinstance(data, dict):
            rows = data.get("data") or data.get("items") or data.get("collections") or []
        else:
            rows = data or []
        items: list[CollectionItem] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            it = parse_collection(raw, site=self.site)
            if it.item_id:
                items.append(it)
        return items

    def collection_detail(self, item_id: str) -> CollectionItem:
        payload = self._request_json(f"/model/{item_id}")
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise PlayboxAPIError(f"unexpected detail payload for {item_id}")
        return parse_collection(data, site=self.site)

    def collection_detail_raw(self, item_id: str) -> dict[str, Any]:
        payload = self._request_json(f"/model/{item_id}")
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise PlayboxAPIError(f"unexpected detail payload for {item_id}")
        return data

    def expand_modal_items(
        self,
        raw: dict[str, Any],
        *,
        include_extend: bool = True,
        include_related: bool = False,
        max_extend: Optional[int] = None,
        max_related: Optional[int] = None,
    ) -> list[CollectionItem]:
        """Expand click-modal payload into root + gallery variants.

        The Explore card is one collection. Clicking open loads detail which often
        includes:
          - root: the selected card (character / cover / video)
          - extend[]: many more full generations shown in the modal gallery
            (each has its own character + cover + video)
          - related[]: similar collections (optional)
        """
        root = parse_collection(raw, site=self.site)
        out: list[CollectionItem] = [root]
        seen = {root.item_id}

        def add_from_list(rows: Any, limit: Optional[int]) -> None:
            if not isinstance(rows, list):
                return
            n = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                it = parse_collection(row, site=self.site)
                if not it.item_id or it.item_id in seen:
                    continue
                # skip empty media
                if not it.media_map():
                    continue
                seen.add(it.item_id)
                out.append(it)
                n += 1
                if limit is not None and n >= limit:
                    break

        if include_extend:
            add_from_list(raw.get("extend"), max_extend)
        if include_related:
            add_from_list(raw.get("related"), max_related)
        return out

    def crawl_explore(
        self,
        *,
        pages: int = 1,
        page_from: int = 1,
        max_items: Optional[int] = None,
        category_id: str = "",
        search: str = "",
        keyword: str = "",
        sleep: float = 0.35,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> list[CollectionItem]:
        """Paginate Explore with de-dup by item_id."""
        log = on_progress or (lambda _m: None)
        seen: dict[str, CollectionItem] = {}
        page_from = max(1, page_from)
        page_to = page_from + max(1, pages) - 1

        for page in range(page_from, page_to + 1):
            if max_items is not None and len(seen) >= max_items:
                break
            log(f"[explore] page={page} category={category_id or '-'} search={search or '-'}")
            try:
                batch = self.explore(
                    page=page,
                    category_id=category_id,
                    search=search,
                    keyword=keyword,
                )
            except PlayboxAPIError as exc:
                log(f"[explore] page={page} failed: {exc}")
                break
            if not batch:
                log(f"[explore] page={page} empty, stop")
                break
            new = 0
            for it in batch:
                if it.item_id not in seen:
                    seen[it.item_id] = it
                    new += 1
                    if max_items is not None and len(seen) >= max_items:
                        break
            log(f"[explore] page={page} got={len(batch)} new={new} total={len(seen)}")
            if page < page_to and sleep > 0:
                time.sleep(sleep)

        items = list(seen.values())
        if max_items is not None:
            items = items[:max_items]
        return items
