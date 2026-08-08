"""CLI for GoodShort batch download & library management."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .api import (
    DEFAULT_CATEGORIES,
    CatalogItem,
    GoodShortAPIError,
    GoodShortClient,
    parse_input,
)
from .downloader import FFmpegDownloader
from .store import Library

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "goodshort.db"
DEFAULT_DOWNLOADS = ROOT / "downloads" / "goodshort"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gsdl",
        description="GoodShort batch downloader & library manager",
    )
    p.add_argument("--version", action="version", version=f"gsdl {__version__}")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite DB path")
    p.add_argument(
        "--download-dir",
        type=Path,
        default=DEFAULT_DOWNLOADS,
        help="Download root directory",
    )
    p.add_argument("--cookie", default="", help="Optional browser Cookie for paid eps")
    p.add_argument("--base-url", default="https://www.goodshort.com")

    sub = p.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="Add drama URL/bookId to library and sync")
    add.add_argument("targets", nargs="+", help="Drama/episode URL or bookId")
    add.add_argument("--note", default="", help="Optional note")

    sync = sub.add_parser("sync", help="Refresh metadata & episode list")
    sync.add_argument("book_ids", nargs="*", help="bookId list; empty = all")

    sub.add_parser("list", help="List dramas in library")

    eps = sub.add_parser("episodes", help="List episodes of a drama")
    eps.add_argument("book_id")
    eps.add_argument(
        "--status",
        default="",
        help="Filter by status: pending,done,failed,locked,...",
    )

    dl = sub.add_parser("download", help="Batch download pending episodes")
    dl.add_argument("book_ids", nargs="*", help="bookId list; empty = all")
    dl.add_argument("--workers", type=int, default=3, help="Concurrent downloads")
    dl.add_argument(
        "--free-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Only free episodes with m3u8 (default: true)",
    )
    dl.add_argument("--include-failed", action="store_true")
    dl.add_argument("--limit", type=int, default=None, help="Max episodes this run")
    dl.add_argument("--from", dest="ep_from", type=int, default=None, help="EP from")
    dl.add_argument("--to", dest="ep_to", type=int, default=None, help="EP to")
    dl.add_argument("--timeout", type=int, default=600, help="ffmpeg timeout seconds")
    dl.add_argument(
        "--reset-stuck",
        action="store_true",
        help="Reset downloading/queued -> pending before run",
    )

    st = sub.add_parser("status", help="Library progress overview")
    st.add_argument("book_id", nargs="?", default=None)

    retry = sub.add_parser("retry", help="Reset failed episodes to pending")
    retry.add_argument("book_id", nargs="?", default=None)

    rm = sub.add_parser("remove", help="Remove drama from library DB (files kept)")
    rm.add_argument("book_id")
    rm.add_argument(
        "--delete-files",
        action="store_true",
        help="Also delete downloaded files for this drama",
    )

    sub.add_parser("export", help="Export library status as TSV")

    cats = sub.add_parser("categories", help="List crawlable catalog categories")
    cats.add_argument(
        "--online",
        action="store_true",
        help="Fetch genre list from site (default: built-in list)",
    )

    crawl = sub.add_parser(
        "crawl",
        help="Auto-pull catalog (names+links), optionally sync into library",
    )
    crawl.add_argument(
        "--category",
        action="append",
        dest="categories",
        default=None,
        help="Category path, e.g. playlets / romance-137-playlets (repeatable)",
    )
    crawl.add_argument(
        "--all-categories",
        action="store_true",
        help="Crawl built-in genre list (playlets + major genres)",
    )
    crawl.add_argument("--pages", type=int, default=1, help="Max pages per category (default 1)")
    crawl.add_argument("--page-from", type=int, default=1, help="Start page (default 1)")
    crawl.add_argument("--page-to", type=int, default=None, help="End page (inclusive)")
    crawl.add_argument("--max-dramas", type=int, default=None, help="Stop after N unique dramas")
    crawl.add_argument("--sleep", type=float, default=0.35, help="Delay between page requests")
    crawl.add_argument(
        "--no-home",
        action="store_true",
        help="Skip homepage columns",
    )
    crawl.add_argument(
        "--ingest",
        action="store_true",
        help="Sync each drama into library (book detail + episodes)",
    )
    crawl.add_argument(
        "--export",
        type=Path,
        default=None,
        help="Write discovered catalog TSV to path",
    )
    crawl.add_argument(
        "--skip-existing",
        action="store_true",
        help="With --ingest, skip dramas already in library",
    )

    auto = sub.add_parser(
        "auto",
        help="Crawl catalog → ingest library → batch download free episodes",
    )
    auto.add_argument(
        "--category",
        action="append",
        dest="categories",
        default=None,
        help="Category path (repeatable). Default: playlets only",
    )
    auto.add_argument(
        "--all-categories",
        action="store_true",
        help="Crawl built-in genre list",
    )
    auto.add_argument("--pages", type=int, default=1, help="Max pages per category")
    auto.add_argument("--page-from", type=int, default=1)
    auto.add_argument("--page-to", type=int, default=None)
    auto.add_argument("--max-dramas", type=int, default=20, help="Max dramas to process (default 20)")
    auto.add_argument("--sleep", type=float, default=0.35)
    auto.add_argument("--no-home", action="store_true")
    auto.add_argument("--skip-existing", action="store_true", help="Skip dramas already in library")
    auto.add_argument("--workers", type=int, default=3)
    auto.add_argument(
        "--free-only",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    auto.add_argument("--limit", type=int, default=None, help="Max episodes to download this run")
    auto.add_argument("--timeout", type=int, default=600)
    auto.add_argument(
        "--dry-run",
        action="store_true",
        help="Only crawl & print catalog, do not ingest/download",
    )

    return p


def make_client(args: argparse.Namespace) -> GoodShortClient:
    return GoodShortClient(base_url=args.base_url, cookie=args.cookie)


def make_library(args: argparse.Namespace) -> Library:
    return Library(args.db)


def cmd_add(args: argparse.Namespace) -> int:
    client = make_client(args)
    lib = make_library(args)
    ok = 0
    for target in args.targets:
        try:
            book_id, _chapter = parse_input(target)
            print(f"[add] resolving {target} -> bookId={book_id}")
            meta = client.book_detail(book_id)
            drama = lib.upsert_drama(meta, note=args.note)
            chapters = client.list_all_chapters(book_id)
            n = lib.replace_episodes(book_id, chapters)
            free = sum(1 for c in chapters if c.is_free and c.m3u8_path)
            locked = sum(1 for c in chapters if not c.m3u8_path)
            print(
                f"  ✓ {meta.book_name} | chapters={n} free_stream={free} "
                f"no_stream={locked} dir={drama.dir_name}"
            )
            ok += 1
        except (ValueError, GoodShortAPIError) as exc:
            print(f"  ✗ {target}: {exc}", file=sys.stderr)
    return 0 if ok else 1


def cmd_sync(args: argparse.Namespace) -> int:
    client = make_client(args)
    lib = make_library(args)
    ids = args.book_ids or [d.book_id for d in lib.list_dramas()]
    if not ids:
        print("library empty; use: gsdl add <url>")
        return 1
    for book_id in ids:
        try:
            print(f"[sync] {book_id}")
            meta = client.book_detail(book_id)
            lib.upsert_drama(meta)
            chapters = client.list_all_chapters(book_id)
            n = lib.replace_episodes(book_id, chapters)
            free = sum(1 for c in chapters if c.m3u8_path)
            print(f"  ✓ {meta.book_name}: {n} episodes, {free} with stream")
        except GoodShortAPIError as exc:
            print(f"  ✗ {book_id}: {exc}", file=sys.stderr)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    lib = make_library(args)
    rows = lib.drama_progress()
    if not rows:
        print("library empty")
        return 0
    print(
        f"{'book_id':<14} {'done':>5} {'pend':>5} {'fail':>5} {'lock':>5} "
        f"{'total':>5}  name"
    )
    for r in rows:
        total = sum(r["stats"].values())
        print(
            f"{r['book_id']:<14} {r['done']:>5} {r['pending']:>5} "
            f"{r['failed']:>5} {r['locked']:>5} {total:>5}  {r['book_name']}"
        )
    return 0


def cmd_episodes(args: argparse.Namespace) -> int:
    lib = make_library(args)
    statuses = [s.strip() for s in args.status.split(",") if s.strip()] or None
    eps = lib.list_episodes(args.book_id, statuses=statuses)
    if not eps:
        print("no episodes (sync first?)")
        return 1
    print(
        f"{'EP':>4} {'status':<12} {'price':>5} {'sec':>5} {'sizeMB':>7}  name"
    )
    for e in eps:
        size_mb = e.file_size / 1024 / 1024 if e.file_size else 0
        print(
            f"{e.ep_index:>4} {e.download_status:<12} {e.price:>5} "
            f"{e.play_time:>5} {size_mb:>7.1f}  {e.chapter_name}"
            + (f"  ERR={e.error[:60]}" if e.error else "")
        )
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    lib = make_library(args)
    if args.reset_stuck:
        n = lib.reset_stuck()
        print(f"reset stuck episodes: {n}")

    book_ids = args.book_ids or None
    if book_ids:
        # auto-add/sync if missing
        client = make_client(args)
        for bid in book_ids:
            if not lib.get_drama(bid):
                print(f"[download] book {bid} not in library, adding...")
                try:
                    meta = client.book_detail(bid)
                    lib.upsert_drama(meta)
                    chapters = client.list_all_chapters(bid)
                    lib.replace_episodes(bid, chapters)
                except GoodShortAPIError as exc:
                    print(f"  ✗ {bid}: {exc}", file=sys.stderr)
                    return 1
    elif not lib.list_dramas():
        print("library empty; use: gsdl add <url>")
        return 1

    batch = lib.claim_download_batch(
        book_ids=book_ids,
        free_only=args.free_only,
        include_failed=args.include_failed,
        limit=args.limit,
        ep_from=args.ep_from,
        ep_to=args.ep_to,
    )
    if not batch:
        print("nothing to download")
        return 0

    print(f"queued {len(batch)} episode(s), workers={args.workers}, free_only={args.free_only}")
    client = make_client(args)
    dl = FFmpegDownloader(
        client=client,
        library=lib,
        download_root=args.download_dir,
        workers=args.workers,
        timeout=args.timeout,
        free_only=args.free_only,
        on_progress=print,
    )
    results = dl.run_batch(batch)
    ok = sum(1 for r in results if r.ok)
    skip = sum(1 for r in results if r.skipped)
    fail = sum(1 for r in results if not r.ok)
    print(f"done: ok={ok} skipped_existing={skip} failed={fail}")
    return 0 if fail == 0 else 2


def cmd_status(args: argparse.Namespace) -> int:
    lib = make_library(args)
    if args.book_id:
        drama = lib.get_drama(args.book_id)
        if not drama:
            print("not found")
            return 1
        stats = lib.stats(args.book_id)
        print(f"{drama.book_name} ({drama.book_id})")
        print(f"  dir: {drama.dir_name}")
        print(f"  synced: {drama.synced_at}")
        for k, v in sorted(stats.items()):
            print(f"  {k}: {v}")
        return 0

    rows = lib.drama_progress()
    if not rows:
        print("library empty")
        return 0
    total_done = sum(r["done"] for r in rows)
    total_fail = sum(r["failed"] for r in rows)
    total_lock = sum(r["locked"] for r in rows)
    total_pend = sum(r["pending"] for r in rows)
    print(f"dramas: {len(rows)}")
    print(f"episodes done={total_done} pending={total_pend} failed={total_fail} locked={total_lock}")
    print(f"db: {args.db}")
    print(f"downloads: {args.download_dir}")
    for r in rows:
        total = sum(r["stats"].values())
        print(
            f"  - {r['book_name']}: {r['done']}/{total} done, "
            f"{r['pending']} pending, {r['locked']} locked, {r['failed']} failed"
        )
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    lib = make_library(args)
    n = lib.retry_failed(args.book_id)
    print(f"reset failed -> pending: {n}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    lib = make_library(args)
    drama = lib.get_drama(args.book_id)
    if not drama:
        print("not found")
        return 1
    if args.delete_files:
        folder = args.download_dir / drama.dir_name
        if folder.exists():
            import shutil

            shutil.rmtree(folder)
            print(f"deleted files: {folder}")
    if lib.remove_drama(args.book_id):
        print(f"removed from library: {drama.book_name}")
        return 0
    return 1


def cmd_export(args: argparse.Namespace) -> int:
    lib = make_library(args)
    print("book_id\tep_index\tchapter_id\tstatus\tprice\tfile_path\tname")
    for d in lib.list_dramas():
        for e in lib.list_episodes(d.book_id):
            print(
                f"{e.book_id}\t{e.ep_index}\t{e.chapter_id}\t{e.download_status}\t"
                f"{e.price}\t{e.file_path or ''}\t{e.chapter_name}"
            )
    return 0


def _resolve_categories(args: argparse.Namespace) -> list[str]:
    if getattr(args, "all_categories", False):
        return list(DEFAULT_CATEGORIES)
    if args.categories:
        return list(args.categories)
    return ["playlets"]


def _export_catalog_tsv(path: Path, items: list[CatalogItem], base_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["book_id\tbook_name\turl\tsource\tpage\tresource"]
    for it in items:
        lines.append(
            f"{it.book_id}\t{it.book_name}\t{it.drama_url(base_url)}\t"
            f"{it.source}\t{it.page}\t{it.book_resource_url}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ingest_catalog_items(
    client: GoodShortClient,
    lib: Library,
    items: list[CatalogItem],
    *,
    skip_existing: bool = False,
    note_prefix: str = "crawl",
) -> list[str]:
    """Sync catalog items into library. Returns book_ids successfully ingested."""
    ok_ids: list[str] = []
    for i, it in enumerate(items, 1):
        if skip_existing and lib.get_drama(it.book_id):
            print(f"  · skip existing {it.book_id} {it.book_name}")
            ok_ids.append(it.book_id)
            continue
        try:
            print(f"[{i}/{len(items)}] ingest {it.book_name} ({it.book_id})")
            meta = client.book_detail(it.book_id)
            note = f"{note_prefix}:{it.source}" if it.source else note_prefix
            lib.upsert_drama(meta, note=note)
            chapters = client.list_all_chapters(it.book_id)
            n = lib.replace_episodes(it.book_id, chapters)
            free = sum(1 for c in chapters if c.m3u8_path)
            print(f"  ✓ episodes={n} free_stream={free} url={it.drama_url(client.base_url)}")
            ok_ids.append(it.book_id)
        except GoodShortAPIError as exc:
            print(f"  ✗ {it.book_id}: {exc}", file=sys.stderr)
    return ok_ids


def cmd_categories(args: argparse.Namespace) -> int:
    if args.online:
        client = make_client(args)
        try:
            cats = client.list_categories_from_playlets()
        except GoodShortAPIError as exc:
            print(f"failed: {exc}", file=sys.stderr)
            return 1
        for resource, desc in cats:
            print(f"{resource}\t{desc}")
        return 0
    for c in DEFAULT_CATEGORIES:
        print(c)
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    client = make_client(args)
    categories = _resolve_categories(args)
    print(
        f"[crawl] categories={categories} pages<={args.pages} "
        f"page_from={args.page_from} max_dramas={args.max_dramas}"
    )
    items = client.crawl_catalog(
        categories=categories,
        max_pages=args.pages,
        max_items=args.max_dramas,
        page_from=args.page_from,
        page_to=args.page_to,
        sleep=args.sleep,
        include_home=not args.no_home,
        on_progress=print,
    )
    if not items:
        print("no dramas discovered")
        return 1

    print(f"\n[crawl] discovered {len(items)} dramas:")
    for it in items[:50]:
        print(f"  {it.book_id}  {it.book_name}  {it.drama_url(client.base_url)}")
    if len(items) > 50:
        print(f"  ... and {len(items) - 50} more")

    if args.export:
        _export_catalog_tsv(args.export, items, client.base_url)
        print(f"[crawl] wrote {args.export}")

    if args.ingest:
        lib = make_library(args)
        ids = ingest_catalog_items(
            client, lib, items, skip_existing=args.skip_existing
        )
        print(f"[crawl] ingested {len(ids)}/{len(items)}")
    else:
        print("[crawl] tip: add --ingest to sync into library, or use: gsdl auto")
    return 0


def cmd_auto(args: argparse.Namespace) -> int:
    """End-to-end: crawl → ingest → download free episodes."""
    client = make_client(args)
    categories = _resolve_categories(args)
    print(
        f"[auto] crawl categories={categories} pages<={args.pages} "
        f"max_dramas={args.max_dramas}"
    )
    items = client.crawl_catalog(
        categories=categories,
        max_pages=args.pages,
        max_items=args.max_dramas,
        page_from=args.page_from,
        page_to=args.page_to,
        sleep=args.sleep,
        include_home=not args.no_home,
        on_progress=print,
    )
    if not items:
        print("no dramas discovered")
        return 1

    print(f"[auto] discovered {len(items)} dramas")
    for it in items:
        print(f"  - {it.book_name} | {it.drama_url(client.base_url)}")

    if args.dry_run:
        print("[auto] dry-run: stop before ingest/download")
        return 0

    lib = make_library(args)
    book_ids = ingest_catalog_items(
        client, lib, items, skip_existing=args.skip_existing, note_prefix="auto"
    )
    if not book_ids:
        print("nothing ingested")
        return 1

    batch = lib.claim_download_batch(
        book_ids=book_ids,
        free_only=args.free_only,
        include_failed=False,
        limit=args.limit,
        ep_from=None,
        ep_to=None,
    )
    if not batch:
        print("[auto] no free pending episodes to download")
        return 0

    print(
        f"[auto] downloading {len(batch)} episode(s), workers={args.workers}, "
        f"free_only={args.free_only}"
    )
    dl = FFmpegDownloader(
        client=client,
        library=lib,
        download_root=args.download_dir,
        workers=args.workers,
        timeout=args.timeout,
        free_only=args.free_only,
        on_progress=print,
    )
    results = dl.run_batch(batch)
    ok = sum(1 for r in results if r.ok)
    fail = sum(1 for r in results if not r.ok)
    print(f"[auto] finished ok={ok} failed={fail}")
    print("[auto] status:")
    # brief progress for processed books
    for bid in book_ids:
        st = lib.stats(bid)
        drama = lib.get_drama(bid)
        name = drama.book_name if drama else bid
        print(
            f"  {name}: done={st.get('done', 0)} pending={st.get('pending', 0)} "
            f"locked={st.get('locked', 0)} failed={st.get('failed', 0)}"
        )
    return 0 if fail == 0 else 2


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "add": cmd_add,
        "sync": cmd_sync,
        "list": cmd_list,
        "episodes": cmd_episodes,
        "download": cmd_download,
        "status": cmd_status,
        "retry": cmd_retry,
        "remove": cmd_remove,
        "export": cmd_export,
        "categories": cmd_categories,
        "crawl": cmd_crawl,
        "auto": cmd_auto,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
