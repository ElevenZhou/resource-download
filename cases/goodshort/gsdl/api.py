"""GoodShort web API client."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import codecs
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import urlencode

DEFAULT_BASE = "https://www.goodshort.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BOOK_ID_RE = re.compile(r"(?<!\d)(31\d{8,})(?!\d)")
DRAMA_URL_RE = re.compile(
    r"goodshort\.com/(?:[a-z]{2}(?:-[A-Za-z]+)?/)?drama/([^/?#]+)",
    re.I,
)
EPISODE_URL_RE = re.compile(
    r"goodshort\.com/(?:[a-z]{2}(?:-[A-Za-z]+)?/)?episode/([^/]+)/([^/?#]+)",
    re.I,
)

# Built-in catalog sources (path under /dramas/...)
DEFAULT_CATEGORIES = (
    "playlets",
    "romance-137-playlets",
    "urban-136-playlets",
    "fantasy-135-playlets",
    "thriller-139-playlets",
    "superpower-140-playlets",
    "ancient-141-playlets",
    "lgbtqia-148-playlets",
    "suspense-149-playlets",
)


@dataclass
class BookMeta:
    book_id: str
    book_name: str
    book_resource_url: str
    chapter_count: int
    cover: str
    introduction: str
    language: str
    write_status: str
    preview_chapter_num: int
    raw: dict[str, Any]


@dataclass
class CatalogItem:
    """Lightweight drama entry from catalog crawl (before full book/detail)."""

    book_id: str
    book_name: str
    book_resource_url: str
    source: str = ""
    page: int = 0
    cover: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def drama_path(self) -> str:
        slug = self.book_resource_url or f"{self.book_id}"
        return f"/drama/{slug}"

    def drama_url(self, base_url: str = DEFAULT_BASE) -> str:
        return f"{base_url.rstrip('/')}{self.drama_path}"


@dataclass
class ChapterMeta:
    chapter_id: str
    book_id: str
    chapter_name: str
    index: int
    chapter_resource_url: str
    price: int
    play_time: int
    m3u8_path: Optional[str]
    status: int
    raw: dict[str, Any]

    @property
    def is_free(self) -> bool:
        return int(self.price or 0) == 0

    @property
    def is_locked(self) -> bool:
        return not self.m3u8_path and not self.is_free


class GoodShortAPIError(RuntimeError):
    pass


class GoodShortClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE,
        cookie: str = "",
        timeout: float = 30.0,
        user_agent: str = DEFAULT_UA,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookie = cookie.strip()
        self.timeout = timeout
        self.user_agent = user_agent

    def _headers(self, *, json_body: bool = True, referer: Optional[str] = None) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Origin": self.base_url,
            "Referer": referer or f"{self.base_url}/",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def get_text(self, path_or_url: str, *, referer: Optional[str] = None) -> str:
        if path_or_url.startswith("http"):
            url = path_or_url
        else:
            url = f"{self.base_url}{path_or_url}"
        req = urllib.request.Request(
            url,
            headers=self._headers(json_body=False, referer=referer),
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise GoodShortAPIError(f"HTTP {exc.code} GET {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GoodShortAPIError(f"Network error GET {url}: {exc}") from exc

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise GoodShortAPIError(f"HTTP {exc.code} {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GoodShortAPIError(f"Network error {path}: {exc}") from exc

        status = payload.get("status")
        if status not in (0, "0", None) and not payload.get("success", False):
            msg = payload.get("message") or payload.get("msg") or "unknown error"
            raise GoodShortAPIError(f"API {path} failed: status={status} message={msg}")
        return payload

    def book_detail(self, book_id: str) -> BookMeta:
        payload = self.post("/hwycreels/book/detail", {"bookId": str(book_id)})
        data = payload.get("data") or {}
        book = data.get("book") or data
        if not book.get("bookId"):
            raise GoodShortAPIError(f"book not found: {book_id}")
        return BookMeta(
            book_id=str(book["bookId"]),
            book_name=str(book.get("bookName") or book_id),
            book_resource_url=str(book.get("bookResourceUrl") or ""),
            chapter_count=int(book.get("chapterCount") or 0),
            cover=str(book.get("cover") or book.get("cover2") or ""),
            introduction=str(book.get("introduction") or ""),
            language=str(book.get("language") or ""),
            write_status=str(book.get("writeStatus") or ""),
            preview_chapter_num=int(book.get("previewChapterNum") or 0),
            raw=book,
        )

    def chapter_page(
        self, book_id: str, page_no: int = 1, page_size: int = 100
    ) -> tuple[list[ChapterMeta], int, int]:
        payload = self.post(
            "/hwycreels/chapter/page",
            {
                "bookId": str(book_id),
                "pageNo": page_no,
                "pageSize": page_size,
            },
        )
        data = payload.get("data") or {}
        records = data.get("records") or []
        total = int(data.get("total") or len(records))
        pages = int(data.get("pages") or 1)
        chapters = [self._map_chapter(book_id, item) for item in records]
        return chapters, total, pages

    def list_all_chapters(self, book_id: str, page_size: int = 100) -> list[ChapterMeta]:
        first, total, pages = self.chapter_page(book_id, page_no=1, page_size=page_size)
        if pages <= 1:
            return first
        all_chapters = list(first)
        for page in range(2, pages + 1):
            items, _, _ = self.chapter_page(book_id, page_no=page, page_size=page_size)
            all_chapters.extend(items)
        # Deduplicate by chapter id, keep order
        seen: set[str] = set()
        ordered: list[ChapterMeta] = []
        for ch in all_chapters:
            if ch.chapter_id in seen:
                continue
            seen.add(ch.chapter_id)
            ordered.append(ch)
        if total and len(ordered) < total:
            # keep what we got; caller can re-sync later
            pass
        return ordered

    def chapter_detail(self, book_id: str, chapter_id: str) -> ChapterMeta:
        payload = self.post(
            "/hwycreels/chapter/detail",
            {
                "bookId": str(book_id),
                "chapterId": str(chapter_id),
                "num": 16,
            },
        )
        data = payload.get("data") or {}
        if not data:
            raise GoodShortAPIError(
                f"chapter detail empty: book={book_id} chapter={chapter_id}"
            )
        return self._map_chapter(book_id, data)

    def resolve_m3u8(self, book_id: str, chapter_id: str, cached: Optional[str] = None) -> str:
        """Return a fresh m3u8 URL. Prefer chapter/detail for expiry safety."""
        try:
            detail = self.chapter_detail(book_id, chapter_id)
            if detail.m3u8_path:
                return detail.m3u8_path
        except GoodShortAPIError:
            if cached:
                return cached
            raise
        if cached:
            return cached
        raise GoodShortAPIError(
            f"no m3u8 for book={book_id} chapter={chapter_id} (locked or unpaid?)"
        )

    # --- Catalog discovery -------------------------------------------------

    def catalog_page(
        self,
        category: str = "playlets",
        page: int = 1,
        language: str = "en",
    ) -> tuple[list[CatalogItem], int, int]:
        """
        Fetch one SSR catalog page under /dramas/{category}.
        Returns (items, total_pages, total_books_estimate).
        """
        category = category.strip().strip("/")
        if not category:
            category = "playlets"
        qs = urlencode({"page": page})
        path = f"/dramas/{category}?{qs}"
        html = self.get_text(path, referer=f"{self.base_url}/dramas/{category}")
        items = parse_catalog_items_from_html(html, source=category, page=page)
        total_pages, total = parse_catalog_paging_from_html(html)
        if total_pages < 1:
            total_pages = 1 if items else 0
        return items, total_pages, total

    def crawl_catalog(
        self,
        categories: Optional[Iterable[str]] = None,
        *,
        max_pages: Optional[int] = None,
        max_items: Optional[int] = None,
        page_from: int = 1,
        page_to: Optional[int] = None,
        sleep: float = 0.35,
        include_home: bool = True,
        language: str = "en",
        on_progress: Optional[Any] = None,
    ) -> list[CatalogItem]:
        """
        Crawl category listing pages (+ optional homepage columns).
        Deduplicates by book_id, preserves first-seen order.
        """
        log = on_progress or (lambda _msg: None)
        categories = list(categories) if categories is not None else list(DEFAULT_CATEGORIES)
        seen: set[str] = set()
        ordered: list[CatalogItem] = []

        def add_many(batch: list[CatalogItem]) -> bool:
            """Return False if max_items reached."""
            for it in batch:
                if it.book_id in seen:
                    continue
                seen.add(it.book_id)
                ordered.append(it)
                if max_items and len(ordered) >= max_items:
                    return False
            return True

        if include_home:
            try:
                home_items = self.home_catalog(language=language)
                log(f"[crawl] home columns: {len(home_items)} items")
                if not add_many(home_items):
                    return ordered
            except GoodShortAPIError as exc:
                log(f"[crawl] home skipped: {exc}")

        for cat in categories:
            first_page = max(1, int(page_from or 1))
            try:
                first, total_pages, total = self.catalog_page(
                    cat, page=first_page, language=language
                )
            except GoodShortAPIError as exc:
                log(f"[crawl] category {cat} failed: {exc}")
                continue

            end_page = max(1, total_pages or first_page)
            if page_to is not None:
                end_page = min(end_page, int(page_to))
            if max_pages is not None:
                end_page = min(end_page, first_page + int(max_pages) - 1)
            if end_page < first_page:
                end_page = first_page

            log(
                f"[crawl] {cat}: total≈{total or '?'} pages={total_pages}, "
                f"fetching {first_page}..{end_page}"
            )
            if first and not add_many(first):
                return ordered

            for page in range(first_page + 1, end_page + 1):
                if sleep > 0:
                    time.sleep(sleep)
                try:
                    items, _, _ = self.catalog_page(cat, page=page, language=language)
                except GoodShortAPIError as exc:
                    log(f"[crawl] {cat} page {page} failed: {exc}")
                    continue
                before = len(ordered)
                if not add_many(items):
                    log(
                        f"[crawl] {cat} p{page}: +{len(ordered) - before} "
                        f"(unique total {len(ordered)})"
                    )
                    return ordered
                log(
                    f"[crawl] {cat} p{page}: +{len(ordered) - before} "
                    f"(unique total {len(ordered)})"
                )
                if not items:
                    break

        log(f"[crawl] done, unique dramas: {len(ordered)}")
        return ordered

    def home_catalog(self, language: str = "en") -> list[CatalogItem]:
        """Collect dramas from homepage columns (banner + channel lists)."""
        payload = self.post("/hwycreels/home/index", {"language": language})
        data = payload.get("data") or {}
        columns = data.get("pageColumns") or []
        items: list[CatalogItem] = []
        seen: set[str] = set()

        def push(raw: dict[str, Any], source: str) -> None:
            book_id = str(raw.get("bookId") or raw.get("action") or raw.get("sourceId") or "")
            if not book_id or not book_id.isdigit() or book_id in seen:
                return
            name = str(raw.get("bookName") or raw.get("seoBookName") or raw.get("name") or book_id)
            resource = str(raw.get("bookResourceUrl") or "")
            if not resource:
                resource = f"book-{book_id}"
            seen.add(book_id)
            items.append(
                CatalogItem(
                    book_id=book_id,
                    book_name=name,
                    book_resource_url=resource,
                    source=f"home:{source}",
                    page=0,
                    cover=str(raw.get("bannerUrl") or raw.get("cover") or ""),
                    raw=raw,
                )
            )

        for col in columns:
            col_url = str(col.get("columnResourceUrl") or col.get("name") or "column")
            for raw in col.get("items") or []:
                if isinstance(raw, dict):
                    push(raw, col_url)
            # paginate channel if more
            if col.get("more") and col_url and col_url != "banner":
                try:
                    more = self.post(
                        "/hwycreels/home/second/list",
                        {
                            "columnResourceUrl": col_url,
                            "pageNo": 1,
                            "pageSize": 50,
                            "index": 0,
                            "language": language,
                        },
                    )
                    more_data = more.get("data") or {}
                    for raw in more_data.get("items") or []:
                        if isinstance(raw, dict):
                            push(raw, col_url)
                except GoodShortAPIError:
                    pass
        return items

    def list_categories_from_playlets(self) -> list[tuple[str, str]]:
        """Return [(resourceUrl, desc)] genre filters from playlets page."""
        html = self.get_text("/dramas/playlets")
        # from SSR genreEnums
        found = re.findall(
            r'\{"id":(\d+),"type":1,"desc":"([^"]+)","resourceUrl":"([^"]+)"\}',
            html,
        )
        out = [("playlets", "All Playlets")]
        seen = {"playlets"}
        for _id, desc, resource in found:
            if resource in seen:
                continue
            seen.add(resource)
            out.append((resource, desc))
        return out

    @staticmethod
    def _map_chapter(book_id: str, item: dict[str, Any]) -> ChapterMeta:
        chapter_id = str(item.get("id") or item.get("chapterId") or "")
        resource = str(item.get("chapterResourceUrl") or "")
        name = str(item.get("chapterName") or item.get("seoChapterName") or chapter_id)
        # Prefer human episode number from resource/name (001-...), not 0-based API index.
        index = None
        m = re.match(r"^(\d+)-", resource)
        if m:
            index = int(m.group(1))
        elif re.fullmatch(r"\d+", name.strip()):
            index = int(name.strip())
        elif item.get("index") is not None:
            # API index is often 0-based; convert to 1-based display number.
            index = int(item["index"]) + 1
        else:
            index = 0
        return ChapterMeta(
            chapter_id=chapter_id,
            book_id=str(item.get("bookId") or book_id),
            chapter_name=name,
            index=int(index),
            chapter_resource_url=resource,
            price=int(item.get("price") or 0),
            play_time=int(item.get("playTime") or item.get("wordNum") or 0),
            m3u8_path=item.get("m3u8Path") or None,
            status=int(item.get("status") or 0),
            raw=item,
        )


def _decode_js_string(value: str) -> str:
    # Decode JSON/JS string fragment (handles \uXXXX and \/).
    try:
        return json.loads(f'"{value}"')
    except Exception:
        try:
            return codecs.decode(value.replace("\\/", "/"), "unicode_escape")
        except Exception:
            return value.replace("\\/", "/")


def parse_catalog_items_from_html(
    html: str, *, source: str = "", page: int = 0
) -> list[CatalogItem]:
    """Parse drama entries from SSR HTML (__INITIAL_STATE__ / anchors)."""
    items: list[CatalogItem] = []
    seen: set[str] = set()

    # Primary: bookId + bookName pairs, then nearby bookResourceUrl
    for m in re.finditer(r'"bookId":"?(\d{10,})"?,"bookName":"((?:\\.|[^"\\])*)"', html):
        book_id = m.group(1)
        if book_id in seen:
            continue
        name = _decode_js_string(m.group(2))
        frag = html[m.end() : m.end() + 3000]
        url_m = re.search(r'"bookResourceUrl":"([^"]+)"', frag)
        resource = url_m.group(1) if url_m else f"book-{book_id}"
        cover_m = re.search(r'"cover":"((?:\\.|[^"\\])*)"', frag)
        cover = _decode_js_string(cover_m.group(1)).replace("\\/", "/") if cover_m else ""
        seen.add(book_id)
        items.append(
            CatalogItem(
                book_id=book_id,
                book_name=name,
                book_resource_url=resource,
                source=source,
                page=page,
                cover=cover,
            )
        )

    # Fallback: /drama/slug-bookId anchors if state parse empty
    if not items:
        for resource in re.findall(r"/drama/([a-zA-Z0-9\-]+-31\d+)", html):
            book_id = resource.rsplit("-", 1)[-1]
            if book_id in seen:
                continue
            seen.add(book_id)
            items.append(
                CatalogItem(
                    book_id=book_id,
                    book_name=book_id,
                    book_resource_url=resource,
                    source=source,
                    page=page,
                )
            )
    return items


def parse_catalog_paging_from_html(html: str) -> tuple[int, int]:
    """Return (total_pages, total_items) best-effort from SSR state."""
    total_pages = 0
    total = 0
    m = re.search(r'"totalPage":(\d+)', html)
    if m:
        total_pages = int(m.group(1))
    # Prefer Browse.total if present
    browse = re.search(r'"Browse":\{.*?"total":(\d+)', html)
    if browse:
        total = int(browse.group(1))
    else:
        totals = [int(x) for x in re.findall(r'"total":(\d+)', html)]
        total = max(totals) if totals else 0
    if total_pages <= 0:
        pages = [int(p) for p in re.findall(r"[?&]page=(\d+)", html)]
        total_pages = max(pages) if pages else 0
    return total_pages, total


def parse_input(text: str) -> tuple[str, Optional[str]]:
    """
    Parse user input into (book_id, chapter_id?).
    Accepts raw bookId, drama URL, or episode URL.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty input")

    ep = EPISODE_URL_RE.search(text)
    if ep:
        book_part, chapter_part = ep.group(1), ep.group(2)
        book_id = extract_book_id(book_part) or extract_book_id(text)
        chapter_id = None
        m = re.search(r"-(\d+)$", chapter_part)
        if m:
            chapter_id = m.group(1)
        if not book_id:
            raise ValueError(f"cannot parse bookId from episode URL: {text}")
        return book_id, chapter_id

    drama = DRAMA_URL_RE.search(text)
    if drama:
        book_id = extract_book_id(drama.group(1)) or extract_book_id(text)
        if not book_id:
            raise ValueError(f"cannot parse bookId from drama URL: {text}")
        return book_id, None

    book_id = extract_book_id(text)
    if book_id and text.replace(" ", "") == book_id:
        return book_id, None
    if book_id:
        return book_id, None

    raise ValueError(f"unsupported GoodShort input: {text}")


def extract_book_id(text: str) -> Optional[str]:
    m = BOOK_ID_RE.search(text)
    return m.group(1) if m else None
