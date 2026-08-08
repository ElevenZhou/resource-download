"""CLI for Playbox explore crawl & batch media download."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from . import __version__
from .api import PlayboxAPIError, PlayboxClient
from .downloader import MediaDownloader
from .store import Library

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "playbox.db"
DEFAULT_DOWNLOADS = ROOT / "downloads"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pbdl",
        description="Playbox explore crawler & batch media downloader",
    )
    p.add_argument("--version", action="version", version=f"pbdl {__version__}")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite DB path")
    p.add_argument(
        "--download-dir",
        type=Path,
        default=DEFAULT_DOWNLOADS,
        help="Download root directory",
    )
    p.add_argument("--api-base", default="https://api.playbox.com/api")
    p.add_argument("--site", default="https://www.playbox.com")

    sub = p.add_subparsers(dest="cmd", required=True)

    crawl = sub.add_parser("crawl", help="Crawl Explore list into library")
    crawl.add_argument("--pages", type=int, default=1, help="Pages to fetch (default 1)")
    crawl.add_argument("--page-from", type=int, default=1)
    crawl.add_argument("--max-items", type=int, default=None, help="Stop after N items")
    crawl.add_argument("--category", default="", help="categoryId path segment")
    crawl.add_argument("--search", default="")
    crawl.add_argument("--keyword", default="")
    crawl.add_argument("--sleep", type=float, default=0.35)
    crawl.add_argument(
        "--export",
        type=Path,
        default=None,
        help="Also write JSONL of discovered items",
    )
    crawl.add_argument(
        "--dry-run",
        action="store_true",
        help="Print only, do not write DB",
    )
    crawl.add_argument(
        "--with-extend",
        action="store_true",
        help="Open each card detail (modal) and ingest extend[] gallery variants",
    )
    crawl.add_argument(
        "--with-related",
        action="store_true",
        help="Also ingest related[] from detail (implies --with-extend path)",
    )
    crawl.add_argument(
        "--max-extend",
        type=int,
        default=None,
        help="Cap extend[] variants per card (default: all)",
    )

    ls = sub.add_parser("list", help="List items in library")
    ls.add_argument("--limit", type=int, default=50)

    show = sub.add_parser("show", help="Show one item + assets")
    show.add_argument("item_id")

    dl = sub.add_parser("download", help="Batch download pending assets")
    dl.add_argument("item_ids", nargs="*", help="Optional item id filter")
    dl.add_argument("--workers", type=int, default=4)
    dl.add_argument(
        "--kinds",
        default="character,cover,video",
        help="Comma list: character,cover,video",
    )
    dl.add_argument("--include-failed", action="store_true")
    dl.add_argument("--limit", type=int, default=None, help="Max assets this run")
    dl.add_argument("--timeout", type=int, default=120)
    dl.add_argument(
        "--reset-stuck",
        action="store_true",
        help="Reset queued/downloading -> pending first",
    )
    dl.add_argument(
        "--no-refresh",
        action="store_true",
        help="Do not re-fetch detail on download failure",
    )

    auto = sub.add_parser("auto", help="Crawl Explore then download media")
    auto.add_argument("--pages", type=int, default=1)
    auto.add_argument("--page-from", type=int, default=1)
    auto.add_argument("--max-items", type=int, default=10)
    auto.add_argument("--category", default="")
    auto.add_argument("--search", default="")
    auto.add_argument("--keyword", default="")
    auto.add_argument("--sleep", type=float, default=0.35)
    auto.add_argument("--workers", type=int, default=4)
    auto.add_argument("--kinds", default="character,cover,video")
    auto.add_argument("--limit", type=int, default=None, help="Max assets to download")
    auto.add_argument("--timeout", type=int, default=120)
    auto.add_argument("--dry-run", action="store_true")
    auto.add_argument(
        "--with-extend",
        action="store_true",
        help="Expand modal extend[] gallery for each card before download",
    )
    auto.add_argument("--with-related", action="store_true")
    auto.add_argument("--max-extend", type=int, default=None)

    st = sub.add_parser("status", help="Library / download progress")
    st.add_argument("item_id", nargs="?", default=None)

    retry = sub.add_parser("retry", help="failed assets -> pending")
    retry.add_argument("item_id", nargs="?", default=None)

    exp = sub.add_parser("export", help="Export library as JSONL")
    exp.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path (default stdout)",
    )

    rm = sub.add_parser("remove", help="Remove item from DB")
    rm.add_argument("item_id")
    rm.add_argument(
        "--delete-files",
        action="store_true",
        help="Also delete download directory",
    )

    resolve = sub.add_parser(
        "resolve",
        help="Open detail modal API for one item; default expands extend[] gallery",
    )
    resolve.add_argument("item_id")
    resolve.add_argument(
        "--no-extend",
        action="store_true",
        help="Only refresh root card, skip extend[] gallery",
    )
    resolve.add_argument("--with-related", action="store_true")
    resolve.add_argument("--max-extend", type=int, default=None)

    tags = sub.add_parser("tags", help="List tags with item counts")
    tags.add_argument("--limit", type=int, default=100)

    kws = sub.add_parser("keywords", help="List content keywords with counts")
    kws.add_argument("--limit", type=int, default=100)

    cats = sub.add_parser("categories", help="List categories with counts")
    cats.add_argument("--limit", type=int, default=50)

    search = sub.add_parser("search", help="Search library by tag/keyword/creator/text")
    search.add_argument("--tag", default="", help="Exact tag, e.g. VIP")
    search.add_argument("--keyword", default="", help="Exact keyword, e.g. POV")
    search.add_argument("--category", default="", help="Exact category, e.g. Trending")
    search.add_argument("--username", default="", help="Creator username contains")
    search.add_argument("--template", default="", help="Template/model name contains")
    search.add_argument("-q", default="", help="Free text over name/tags/desc/...")
    search.add_argument("--limit", type=int, default=50)

    meta = sub.add_parser("meta", help="Write meta.json (text fields) into download folders")
    meta.add_argument("item_ids", nargs="*", help="Item ids; empty = all")

    return p


def make_client(args: argparse.Namespace) -> PlayboxClient:
    return PlayboxClient(api_base=args.api_base, site=args.site)


def make_library(args: argparse.Namespace) -> Library:
    return Library(args.db)


def _parse_kinds(s: str) -> list[str]:
    kinds = [k.strip() for k in s.split(",") if k.strip()]
    allowed = {"character", "cover", "video"}
    bad = [
        k
        for k in kinds
        if k not in allowed and not k.startswith("character_")
    ]
    if bad:
        raise SystemExit(
            f"unknown kinds: {bad}; allowed: character[,character_N], cover, video"
        )
    return kinds


def _print_item_line(it) -> None:
    tags = []
    try:
        tags = json.loads(it.tags_json or "[]")
    except json.JSONDecodeError:
        pass
    tag_s = ",".join(tags[:5])
    print(
        f"{it.item_id}  @{it.username:16}  {it.name[:40]:40}  "
        f"tags={tag_s or '-'}"
    )


def _ingest_expanded(
    client: PlayboxClient,
    lib: Library,
    root_ids: list[str],
    *,
    include_extend: bool,
    include_related: bool,
    max_extend: Optional[int],
    sleep: float,
    note_prefix: str,
) -> int:
    """Fetch modal detail for each root id and upsert root+extend(+related)."""
    total = 0
    for i, rid in enumerate(root_ids, 1):
        try:
            raw = client.collection_detail_raw(rid)
        except PlayboxAPIError as exc:
            print(f"  ✗ detail {rid}: {exc}", file=sys.stderr)
            continue
        bundle = client.expand_modal_items(
            raw,
            include_extend=include_extend,
            include_related=include_related,
            max_extend=max_extend,
        )
        print(
            f"  [{i}/{len(root_ids)}] modal {rid[:10]}… "
            f"-> {len(bundle)} item(s) (extend/related expanded={include_extend})"
        )
        # ensure root first so children can nest under its dir_name
        for j, it in enumerate(bundle):
            if it.item_id == rid:
                lib.upsert_item(it, note=note_prefix, parent_id="", gallery_index=0)
                total += 1
                break
        gidx = 0
        for it in bundle:
            if it.item_id == rid:
                continue
            gidx += 1
            lib.upsert_item(
                it,
                note=f"{note_prefix}:extend_of:{rid}",
                parent_id=rid,
                gallery_index=gidx,
            )
            total += 1
        if sleep > 0 and i < len(root_ids):
            time.sleep(sleep)
    return total


def cmd_crawl(args: argparse.Namespace) -> int:
    client = make_client(args)
    print(
        f"[crawl] pages={args.pages} page_from={args.page_from} "
        f"max_items={args.max_items} category={args.category or '-'} "
        f"with_extend={getattr(args, 'with_extend', False)}"
    )
    try:
        items = client.crawl_explore(
            pages=args.pages,
            page_from=args.page_from,
            max_items=args.max_items,
            category_id=args.category,
            search=args.search,
            keyword=args.keyword,
            sleep=args.sleep,
            on_progress=print,
        )
    except PlayboxAPIError as exc:
        print(f"crawl failed: {exc}", file=sys.stderr)
        return 1

    if not items:
        print("no items discovered")
        return 1

    print(f"\n[crawl] discovered {len(items)} explore cards:")
    for it in items[:40]:
        media = ",".join(it.media_map().keys()) or "-"
        print(
            f"  {it.item_id}  @{it.username}  {it.name[:48]}  "
            f"[{media}]  tags={','.join(it.tags[:4]) or '-'}"
        )
    if len(items) > 40:
        print(f"  ... and {len(items) - 40} more")

    expand = bool(getattr(args, "with_extend", False) or getattr(args, "with_related", False))

    if args.export and not expand:
        args.export.parent.mkdir(parents=True, exist_ok=True)
        with args.export.open("w", encoding="utf-8") as f:
            for it in items:
                rec = {
                    "item_id": it.item_id,
                    "name": it.name,
                    "username": it.username,
                    "character_image": it.character_image,
                    "cover_image": it.cover_image,
                    "video_url": it.best_video,
                    "tags": it.tags,
                    "keywords": it.keywords,
                    "page_url": it.page_url,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[crawl] wrote {args.export}")

    if args.dry_run:
        if expand:
            print("[crawl] dry-run: would open modal detail + expand extend[] for each card")
        else:
            print("[crawl] dry-run: DB not updated")
        return 0

    lib = make_library(args)
    if expand:
        n = _ingest_expanded(
            client,
            lib,
            [it.item_id for it in items],
            include_extend=True,
            include_related=bool(getattr(args, "with_related", False)),
            max_extend=getattr(args, "max_extend", None),
            sleep=args.sleep,
            note_prefix="crawl:modal",
        )
        print(f"[crawl] upserted {n} items (cards+modal gallery) into {args.db}")
    else:
        for it in items:
            lib.upsert_item(it, note="crawl:explore")
        print(f"[crawl] upserted {len(items)} into {args.db}")
        print("[crawl] tip: use --with-extend to also grab popup gallery (extend[])")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    lib = make_library(args)
    rows = lib.list_items(limit=args.limit)
    if not rows:
        print("library empty; run: pbdl crawl")
        return 0
    for it in rows:
        _print_item_line(it)
    print(f"({len(rows)} items)")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    lib = make_library(args)
    it = lib.get_item(args.item_id)
    if not it:
        print("not found", file=sys.stderr)
        return 1
    print(f"id:         {it.item_id}")
    print(f"name:       {it.name}")
    print(f"creator:    @{it.username}  (user_id={it.user_id})")
    print(f"template:   {it.template_name or it.model_name or '-'}")
    print(f"tmpl_author:{it.template_creator or '-'}")
    print(f"model:      {it.model_name or '-'}  type={it.model_type}  id={it.model_id}")
    print(f"tags:       {it.tags_json}")
    print(f"keywords:   {it.keywords_json}")
    print(f"categories: {it.categories_json}")
    if it.description:
        print(f"desc:       {it.description[:200]}")
    if it.custom_prompt:
        print(f"prompt:     {it.custom_prompt[:200]}")
    print(f"character:  {(it.character_image or '')[:120]}")
    print(f"cover:      {(it.cover_image or '')[:120]}")
    print(f"video:      {(it.video_url or '')[:120]}")
    print(f"dir:        {it.dir_name}")
    print(f"parent:     {it.parent_id or '-'}  gallery_index={it.gallery_index}")
    print("assets:")
    for a in lib.list_assets(item_id=it.item_id):
        print(
            f"  {a.kind:10} {a.download_status:12} "
            f"{a.file_size:>10}  {a.file_path or a.url[:60]}"
        )
    return 0


def cmd_tags(args: argparse.Namespace) -> int:
    lib = make_library(args)
    rows = lib.list_tags(limit=args.limit)
    if not rows:
        print("no tags yet; crawl/resolve first")
        return 0
    for name, n in rows:
        print(f"{n:>5}  {name}")
    return 0


def cmd_keywords(args: argparse.Namespace) -> int:
    lib = make_library(args)
    rows = lib.list_keywords(limit=args.limit)
    if not rows:
        print("no keywords yet")
        return 0
    for name, n in rows:
        print(f"{n:>5}  {name}")
    return 0


def cmd_categories(args: argparse.Namespace) -> int:
    lib = make_library(args)
    rows = lib.list_categories(limit=args.limit)
    if not rows:
        print("no categories yet")
        return 0
    for name, n in rows:
        print(f"{n:>5}  {name}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    lib = make_library(args)
    rows = lib.search_items(
        tag=args.tag,
        keyword=args.keyword,
        category=args.category,
        username=args.username,
        template=args.template,
        q=args.q,
        limit=args.limit,
    )
    if not rows:
        print("no matches")
        return 0
    for it in rows:
        tmpl = it.template_name or it.model_name or "-"
        print(
            f"{it.item_id}  @{it.username:14}  {it.name[:36]:36}  "
            f"tmpl={tmpl[:28]}  tags={it.tags_json}"
        )
    print(f"({len(rows)} hits)")
    return 0


def cmd_meta(args: argparse.Namespace) -> int:
    """Write / refresh meta.json for items under download dir."""
    lib = make_library(args)
    ids = args.item_ids or [r.item_id for r in lib.list_items()]
    n = 0
    for iid in ids:
        path = lib.write_meta_json(iid, args.download_dir)
        if path:
            print(f"  wrote {path}")
            n += 1
    print(f"meta.json written for {n} item(s)")
    return 0 if n else 1


def cmd_download(args: argparse.Namespace) -> int:
    lib = make_library(args)
    if args.reset_stuck:
        n = lib.reset_stuck()
        print(f"[download] reset stuck: {n}")

    kinds = _parse_kinds(args.kinds)
    batch = lib.claim_assets(
        item_ids=args.item_ids or None,
        kinds=kinds,
        include_failed=args.include_failed,
        limit=args.limit,
    )
    if not batch:
        print("nothing to download")
        return 0

    print(
        f"[download] assets={len(batch)} workers={args.workers} kinds={kinds}"
    )
    client = make_client(args)
    dl = MediaDownloader(
        client=client,
        library=lib,
        download_root=args.download_dir,
        workers=args.workers,
        timeout=args.timeout,
        refresh_on_fail=not args.no_refresh,
        on_progress=print,
    )
    results = dl.run_batch(batch)
    ok = sum(1 for r in results if r.ok)
    fail = sum(1 for r in results if not r.ok)
    skip = sum(1 for r in results if r.skipped)
    print(f"[download] done ok={ok} skipped={skip} failed={fail}")
    return 0 if fail == 0 else 2


def cmd_auto(args: argparse.Namespace) -> int:
    # crawl
    c_args = argparse.Namespace(**vars(args))
    c_args.export = None
    c_args.dry_run = args.dry_run
    if not hasattr(c_args, "with_extend"):
        c_args.with_extend = False
    if not hasattr(c_args, "with_related"):
        c_args.with_related = False
    if not hasattr(c_args, "max_extend"):
        c_args.max_extend = None
    rc = cmd_crawl(c_args)
    if rc != 0 or args.dry_run:
        return rc

    # download all pending from this library (optionally limited)
    d_args = argparse.Namespace(
        db=args.db,
        download_dir=args.download_dir,
        api_base=args.api_base,
        site=args.site,
        item_ids=[],
        workers=args.workers,
        kinds=args.kinds,
        include_failed=False,
        limit=args.limit,
        timeout=args.timeout,
        reset_stuck=False,
        no_refresh=False,
    )
    return cmd_download(d_args)


def cmd_status(args: argparse.Namespace) -> int:
    lib = make_library(args)
    st = lib.stats(args.item_id)
    print(f"items: {st.get('items', 0)}")
    for k in ("pending", "queued", "downloading", "done", "failed", "skipped"):
        if k in st:
            print(f"  assets {k}: {st[k]}")
    extra = {k: v for k, v in st.items() if k not in ("items", "pending", "queued", "downloading", "done", "failed", "skipped")}
    for k, v in extra.items():
        print(f"  assets {k}: {v}")
    print(f"db: {args.db}")
    print(f"downloads: {args.download_dir}")
    if args.item_id:
        it = lib.get_item(args.item_id)
        if it:
            print(f"item: {it.name} @{it.username}")
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    lib = make_library(args)
    n = lib.retry_failed(args.item_id)
    print(f"retry reset: {n}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    lib = make_library(args)
    rows = lib.list_items()
    lines = []
    for it in rows:
        assets = {
            a.kind: {
                "status": a.download_status,
                "url": a.url,
                "path": a.file_path,
                "size": a.file_size,
            }
            for a in lib.list_assets(item_id=it.item_id)
        }
        lines.append(
            json.dumps(
                {
                    "item_id": it.item_id,
                    "name": it.name,
                    "username": it.username,
                    "template_name": it.template_name,
                    "template_creator": it.template_creator,
                    "model_name": it.model_name,
                    "model_id": it.model_id,
                    "model_type": it.model_type,
                    "description": it.description,
                    "custom_prompt": it.custom_prompt,
                    "tags": json.loads(it.tags_json or "[]"),
                    "keywords": json.loads(it.keywords_json or "[]"),
                    "categories": json.loads(it.categories_json or "[]"),
                    "page_url": it.page_url,
                    "parent_id": it.parent_id,
                    "dir_name": it.dir_name,
                    "assets": assets,
                },
                ensure_ascii=False,
            )
        )
    text = "\n".join(lines) + ("\n" if lines else "")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output} ({len(rows)} items)")
    else:
        sys.stdout.write(text)
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    lib = make_library(args)
    it = lib.get_item(args.item_id)
    if not it:
        print("not found", file=sys.stderr)
        return 1
    if args.delete_files:
        d = args.download_dir / it.dir_name
        if d.is_dir():
            import shutil

            shutil.rmtree(d)
            print(f"deleted files {d}")
    if lib.remove_item(args.item_id):
        print(f"removed {args.item_id}")
        return 0
    return 1


def cmd_resolve(args: argparse.Namespace) -> int:
    client = make_client(args)
    lib = make_library(args)
    try:
        raw = client.collection_detail_raw(args.item_id)
    except PlayboxAPIError as exc:
        print(f"resolve failed: {exc}", file=sys.stderr)
        return 1

    include_extend = not args.no_extend
    bundle = client.expand_modal_items(
        raw,
        include_extend=include_extend,
        include_related=bool(args.with_related),
        max_extend=args.max_extend,
    )
    root_id = args.item_id
    # Prefer the requested id as root (bundle[0] is usually that)
    for it in bundle:
        if it.item_id == root_id:
            lib.upsert_item(it, note="resolve:modal", parent_id="", gallery_index=0)
            break
    else:
        lib.upsert_item(bundle[0], note="resolve:modal", parent_id="", gallery_index=0)
        root_id = bundle[0].item_id

    gidx = 0
    for it in bundle:
        if it.item_id == root_id:
            continue
        gidx += 1
        lib.upsert_item(
            it,
            note=f"resolve:extend_of:{root_id}",
            parent_id=root_id,
            gallery_index=gidx,
        )

    root = next((x for x in bundle if x.item_id == root_id), bundle[0])
    print(f"✓ root: {root.name} @{root.username}")
    print(
        f"  character={bool(root.character_image)} cover={bool(root.cover_image)} "
        f"video={bool(root.best_video)} tags={root.tags}"
    )
    print(
        f"  modal expanded items={len(bundle)} "
        f"(extend={'on' if include_extend else 'off'}, related={'on' if args.with_related else 'off'})"
    )
    if len(bundle) > 1:
        print("  gallery samples:")
        for it in bundle[1:6]:
            media = ",".join(it.media_map().keys()) or "-"
            print(f"    {it.item_id}  @{it.username}  {it.name[:40]}  [{media}]")
        if len(bundle) > 6:
            print(f"    ... +{len(bundle) - 6} more")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "crawl": cmd_crawl,
        "list": cmd_list,
        "show": cmd_show,
        "download": cmd_download,
        "auto": cmd_auto,
        "status": cmd_status,
        "retry": cmd_retry,
        "export": cmd_export,
        "remove": cmd_remove,
        "resolve": cmd_resolve,
        "tags": cmd_tags,
        "keywords": cmd_keywords,
        "categories": cmd_categories,
        "search": cmd_search,
        "meta": cmd_meta,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
