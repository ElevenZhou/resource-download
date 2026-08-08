"""SQLite library for Playbox collections & assets."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .api import CollectionItem


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_name(name: str, max_len: int = 80) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(". ")
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:max_len]


@dataclass
class ItemRow:
    item_id: str
    name: str
    username: str
    user_id: str
    character_image: str
    cover_image: str
    video_url: str
    tags_json: str
    keywords_json: str
    categories_json: str
    model_type: str
    model_id: str
    model_name: str
    template_name: str
    template_creator: str
    description: str
    custom_prompt: str
    page_url: str
    dir_name: str
    parent_id: str
    gallery_index: int
    status: str
    added_at: str
    synced_at: Optional[str]
    note: str


@dataclass
class AssetRow:
    asset_id: str  # f"{item_id}:{kind}"
    item_id: str
    kind: str  # character | cover | video
    url: str
    download_status: str
    file_path: Optional[str]
    file_size: int
    error: Optional[str]
    attempts: int
    updated_at: str


class Library:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS items (
                    item_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    username TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    character_image TEXT NOT NULL DEFAULT '',
                    cover_image TEXT NOT NULL DEFAULT '',
                    video_url TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    categories_json TEXT NOT NULL DEFAULT '[]',
                    model_type TEXT NOT NULL DEFAULT '',
                    page_url TEXT NOT NULL DEFAULT '',
                    dir_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'discovered',
                    added_at TEXT NOT NULL,
                    synced_at TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    download_status TEXT NOT NULL DEFAULT 'pending',
                    file_path TEXT,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES items(item_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_assets_item ON assets(item_id);
                CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(download_status);
                CREATE INDEX IF NOT EXISTS idx_items_username ON items(username);
                """
            )
            # migrations for older DBs / columns added later
            cols = {r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
            for col, decl in (
                ("parent_id", "TEXT NOT NULL DEFAULT ''"),
                ("gallery_index", "INTEGER NOT NULL DEFAULT 0"),
                ("model_id", "TEXT NOT NULL DEFAULT ''"),
                ("model_name", "TEXT NOT NULL DEFAULT ''"),
                ("template_name", "TEXT NOT NULL DEFAULT ''"),
                ("template_creator", "TEXT NOT NULL DEFAULT ''"),
                ("description", "TEXT NOT NULL DEFAULT ''"),
                ("custom_prompt", "TEXT NOT NULL DEFAULT ''"),
            ):
                if col not in cols:
                    conn.execute(f"ALTER TABLE items ADD COLUMN {col} {decl}")

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tags (
                    tag TEXT PRIMARY KEY,
                    item_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS keywords (
                    keyword TEXT PRIMARY KEY,
                    item_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS categories (
                    category TEXT PRIMARY KEY,
                    item_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS item_tags (
                    item_id TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    PRIMARY KEY (item_id, tag),
                    FOREIGN KEY(item_id) REFERENCES items(item_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS item_keywords (
                    item_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    PRIMARY KEY (item_id, keyword),
                    FOREIGN KEY(item_id) REFERENCES items(item_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS item_categories (
                    item_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    PRIMARY KEY (item_id, category),
                    FOREIGN KEY(item_id) REFERENCES items(item_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_items_parent ON items(parent_id);
                CREATE INDEX IF NOT EXISTS idx_items_template ON items(template_name);
                CREATE INDEX IF NOT EXISTS idx_items_creator ON items(username);
                CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag);
                CREATE INDEX IF NOT EXISTS idx_item_keywords_kw ON item_keywords(keyword);
                """
            )

    @staticmethod
    def make_leaf_name(item: CollectionItem) -> str:
        """Single folder segment: {user}_{title}_{id10}."""
        return (
            f"{safe_name(item.username or 'user')}_"
            f"{safe_name(item.name)}_{item.item_id[:10]}"
        )

    def upsert_item(
        self,
        item: CollectionItem,
        note: str = "",
        *,
        parent_id: str = "",
        gallery_index: int = 0,
    ) -> ItemRow:
        """Insert/update item. Gallery children nest under parent_id/gallery/NNN_..."""
        now = utc_now()
        leaf = self.make_leaf_name(item)
        parent_id = (parent_id or "").strip()
        gallery_index = max(0, int(gallery_index or 0))

        with self.connect() as conn:
            existing = conn.execute(
                "SELECT dir_name, added_at, note, parent_id, gallery_index FROM items WHERE item_id = ?",
                (item.item_id,),
            ).fetchone()

            old_dir = existing["dir_name"] if existing else ""
            if existing:
                # Keep stable dir once set; allow re-parent into gallery/ on first modal expand
                dir_name = existing["dir_name"]
                added_at = existing["added_at"]
                note = note or existing["note"]
                if parent_id and not (existing["parent_id"] or ""):
                    p = conn.execute(
                        "SELECT dir_name FROM items WHERE item_id=?", (parent_id,)
                    ).fetchone()
                    if p:
                        idx = gallery_index or existing["gallery_index"] or 1
                        dir_name = f"{p['dir_name']}/gallery/{idx:03d}_{leaf}"
                    gallery_index = gallery_index or existing["gallery_index"] or 0
                elif parent_id and existing["parent_id"] == parent_id and gallery_index:
                    # keep nested path; refresh index segment if still flat
                    if "/gallery/" not in dir_name:
                        p = conn.execute(
                            "SELECT dir_name FROM items WHERE item_id=?", (parent_id,)
                        ).fetchone()
                        if p:
                            dir_name = f"{p['dir_name']}/gallery/{gallery_index:03d}_{leaf}"
                    parent_id = existing["parent_id"]
                    gallery_index = gallery_index or existing["gallery_index"]
                else:
                    parent_id = existing["parent_id"] or parent_id
                    gallery_index = existing["gallery_index"] or gallery_index
            else:
                added_at = now
                if parent_id:
                    p = conn.execute(
                        "SELECT dir_name FROM items WHERE item_id=?", (parent_id,)
                    ).fetchone()
                    if p:
                        idx = gallery_index or 1
                        dir_name = f"{p['dir_name']}/gallery/{idx:03d}_{leaf}"
                    else:
                        dir_name = leaf
                else:
                    dir_name = leaf

            # If layout path changed, re-queue done assets so files land in new folders
            if old_dir and old_dir != dir_name:
                conn.execute(
                    """
                    UPDATE assets SET download_status='pending', error=NULL, updated_at=?
                    WHERE item_id=? AND download_status='done'
                    """,
                    (now, item.item_id),
                )

            conn.execute(
                """
                INSERT INTO items (
                    item_id, name, username, user_id, character_image, cover_image,
                    video_url, tags_json, keywords_json, categories_json, model_type,
                    model_id, model_name, template_name, template_creator, description,
                    custom_prompt, page_url, dir_name, parent_id, gallery_index, status,
                    added_at, synced_at, note, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    name=excluded.name,
                    username=excluded.username,
                    user_id=excluded.user_id,
                    character_image=excluded.character_image,
                    cover_image=excluded.cover_image,
                    video_url=excluded.video_url,
                    tags_json=excluded.tags_json,
                    keywords_json=excluded.keywords_json,
                    categories_json=excluded.categories_json,
                    model_type=excluded.model_type,
                    model_id=excluded.model_id,
                    model_name=excluded.model_name,
                    template_name=excluded.template_name,
                    template_creator=excluded.template_creator,
                    description=excluded.description,
                    custom_prompt=excluded.custom_prompt,
                    page_url=excluded.page_url,
                    dir_name=excluded.dir_name,
                    parent_id=CASE
                        WHEN excluded.parent_id != '' THEN excluded.parent_id
                        ELSE items.parent_id
                    END,
                    gallery_index=CASE
                        WHEN excluded.gallery_index > 0 THEN excluded.gallery_index
                        ELSE items.gallery_index
                    END,
                    status='resolved',
                    synced_at=excluded.synced_at,
                    note=CASE WHEN excluded.note != '' THEN excluded.note ELSE items.note END,
                    raw_json=excluded.raw_json
                """,
                (
                    item.item_id,
                    item.name,
                    item.username,
                    item.user_id,
                    item.character_image,
                    item.cover_image,
                    item.best_video,
                    json.dumps(item.tags, ensure_ascii=False),
                    json.dumps(item.keywords, ensure_ascii=False),
                    json.dumps(item.categories, ensure_ascii=False),
                    item.model_type,
                    item.model_id,
                    item.model_name,
                    item.template_name,
                    item.template_creator,
                    item.description,
                    item.custom_prompt,
                    item.page_url,
                    dir_name,
                    parent_id,
                    gallery_index,
                    "resolved",
                    added_at,
                    now,
                    note,
                    json.dumps(item.raw, ensure_ascii=False)[:200000],
                ),
            )
            self._sync_assets(conn, item)
            self._sync_taxonomy(conn, item)

        row = self.get_item(item.item_id)
        assert row is not None
        return row

    def _sync_taxonomy(self, conn: sqlite3.Connection, item: CollectionItem) -> None:
        """Normalize tags / keywords / categories into lookup tables."""
        item_id = item.item_id

        def sync(table: str, link: str, col: str, values: list[str]) -> None:
            clean = []
            seen: set[str] = set()
            for v in values:
                s = str(v).strip()
                if not s or s in seen:
                    continue
                seen.add(s)
                clean.append(s)
            conn.execute(f"DELETE FROM {link} WHERE item_id=?", (item_id,))
            for s in clean:
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({col}, item_count) VALUES (?, 0)",
                    (s,),
                )
                conn.execute(
                    f"INSERT OR IGNORE INTO {link} (item_id, {col}) VALUES (?, ?)",
                    (item_id, s),
                )
            # refresh counts for touched labels
            for s in clean:
                n = conn.execute(
                    f"SELECT COUNT(*) AS c FROM {link} WHERE {col}=?", (s,)
                ).fetchone()["c"]
                conn.execute(
                    f"UPDATE {table} SET item_count=? WHERE {col}=?", (n, s)
                )

        sync("tags", "item_tags", "tag", item.tags)
        sync("keywords", "item_keywords", "keyword", item.keywords)
        sync("categories", "item_categories", "category", item.categories)

    def rebuild_taxonomy_counts(self) -> None:
        with self.connect() as conn:
            for table, link, col in (
                ("tags", "item_tags", "tag"),
                ("keywords", "item_keywords", "keyword"),
                ("categories", "item_categories", "category"),
            ):
                conn.execute(
                    f"""
                    UPDATE {table} SET item_count = (
                        SELECT COUNT(*) FROM {link} WHERE {link}.{col} = {table}.{col}
                    )
                    """
                )

    def list_tags(self, limit: int = 100) -> list[tuple[str, int]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT tag, item_count FROM tags ORDER BY item_count DESC, tag LIMIT ?",
                (limit,),
            ).fetchall()
        return [(r["tag"], r["item_count"]) for r in rows]

    def list_keywords(self, limit: int = 100) -> list[tuple[str, int]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT keyword, item_count FROM keywords ORDER BY item_count DESC, keyword LIMIT ?",
                (limit,),
            ).fetchall()
        return [(r["keyword"], r["item_count"]) for r in rows]

    def list_categories(self, limit: int = 100) -> list[tuple[str, int]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT category, item_count FROM categories ORDER BY item_count DESC, category LIMIT ?",
                (limit,),
            ).fetchall()
        return [(r["category"], r["item_count"]) for r in rows]

    def search_items(
        self,
        *,
        tag: str = "",
        keyword: str = "",
        category: str = "",
        username: str = "",
        template: str = "",
        q: str = "",
        limit: int = 50,
    ) -> list[ItemRow]:
        """Filter library by taxonomy / creator / free text."""
        clauses: list[str] = []
        params: list[Any] = []
        joins: list[str] = []
        if tag:
            joins.append("JOIN item_tags itg ON itg.item_id = items.item_id")
            clauses.append("itg.tag = ?")
            params.append(tag)
        if keyword:
            joins.append("JOIN item_keywords ikw ON ikw.item_id = items.item_id")
            clauses.append("ikw.keyword = ?")
            params.append(keyword)
        if category:
            joins.append("JOIN item_categories ic ON ic.item_id = items.item_id")
            clauses.append("ic.category = ?")
            params.append(category)
        if username:
            clauses.append("items.username LIKE ?")
            params.append(f"%{username}%")
        if template:
            clauses.append(
                "(items.template_name LIKE ? OR items.model_name LIKE ? OR items.name LIKE ?)"
            )
            params.extend([f"%{template}%"] * 3)
        if q:
            clauses.append(
                "(items.name LIKE ? OR items.username LIKE ? OR items.template_name LIKE ? "
                "OR items.template_creator LIKE ? OR items.description LIKE ? "
                "OR items.tags_json LIKE ? OR items.keywords_json LIKE ?)"
            )
            params.extend([f"%{q}%"] * 7)
        sql = "SELECT DISTINCT items.* FROM items " + " ".join(joins)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY items.synced_at DESC LIMIT ?"
        params.append(int(limit))
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._item_row(r) for r in rows]

    def write_meta_json(self, item_id: str, download_root: Path) -> Optional[Path]:
        """Write meta.json beside media files for offline browsing.

        Includes original remote URLs (character / cover / video) as well as
        local file paths after download.
        """
        item = self.get_item(item_id)
        if not item:
            return None
        out_dir = Path(download_root) / item.dir_name
        out_dir.mkdir(parents=True, exist_ok=True)
        assets = self.list_assets(item_id=item_id)
        by_kind = {a.kind: a for a in assets}

        def asset_url(kind: str, fallback: str = "") -> str:
            a = by_kind.get(kind)
            if a and a.url:
                return a.url
            return fallback or ""

        # character may be character / character_2 / ...
        character_urls: dict[str, str] = {}
        for a in assets:
            if a.kind == "character" or a.kind.startswith("character_"):
                if a.url:
                    character_urls[a.kind] = a.url
        if not character_urls and item.character_image:
            character_urls["character"] = item.character_image

        cover_url = asset_url("cover", item.cover_image)
        video_url = asset_url("video", item.video_url)

        meta = {
            "item_id": item.item_id,
            "name": item.name,
            "username": item.username,
            "user_id": item.user_id,
            "template_name": item.template_name or item.model_name or item.name,
            "template_creator": item.template_creator or item.username,
            "model_id": item.model_id,
            "model_name": item.model_name,
            "model_type": item.model_type,
            "description": item.description,
            "custom_prompt": item.custom_prompt,
            "tags": json.loads(item.tags_json or "[]"),
            "keywords": json.loads(item.keywords_json or "[]"),
            "categories": json.loads(item.categories_json or "[]"),
            "page_url": item.page_url,
            "parent_id": item.parent_id,
            "gallery_index": item.gallery_index,
            "dir_name": item.dir_name,
            # Original remote URLs (may include signed token + expires)
            "urls": {
                "character": character_urls.get("character", ""),
                "character_images": character_urls,  # all character* slots
                "cover": cover_url,
                "video": video_url,
            },
            # Flat aliases for convenience
            "character_image_url": character_urls.get("character", ""),
            "cover_image_url": cover_url,
            "video_url": video_url,
            "files": {
                a.kind: {
                    "url": a.url,  # original remote URL
                    "status": a.download_status,
                    "path": a.file_path,
                    "size": a.file_size,
                }
                for a in assets
            },
        }
        path = out_dir / "meta.json"
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def _sync_assets(self, conn: sqlite3.Connection, item: CollectionItem) -> None:
        now = utc_now()
        media = item.media_map()
        # kinds that should exist
        for kind, url in media.items():
            asset_id = f"{item.item_id}:{kind}"
            old = conn.execute(
                "SELECT * FROM assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            if old and old["download_status"] == "done":
                # refresh url but keep done
                conn.execute(
                    """
                    UPDATE assets SET url=?, updated_at=?
                    WHERE asset_id=?
                    """,
                    (url, now, asset_id),
                )
                continue
            status = "pending" if url else "skipped"
            if old and old["download_status"] in ("downloading", "queued"):
                status = old["download_status"]
            conn.execute(
                """
                INSERT INTO assets (
                    asset_id, item_id, kind, url, download_status,
                    file_path, file_size, error, attempts, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    url=excluded.url,
                    download_status=CASE
                        WHEN assets.download_status='done' THEN 'done'
                        ELSE excluded.download_status
                    END,
                    updated_at=excluded.updated_at
                """,
                (
                    asset_id,
                    item.item_id,
                    kind,
                    url,
                    status,
                    old["file_path"] if old else None,
                    old["file_size"] if old else 0,
                    None,
                    old["attempts"] if old else 0,
                    now,
                ),
            )

        # mark missing kinds that were pending as skipped if no longer present
        existing_kinds = set(media.keys())
        for row in conn.execute(
            "SELECT asset_id, kind, download_status FROM assets WHERE item_id=?",
            (item.item_id,),
        ):
            if row["kind"] not in existing_kinds and row["download_status"] == "pending":
                conn.execute(
                    "UPDATE assets SET download_status='skipped', updated_at=? WHERE asset_id=?",
                    (now, row["asset_id"]),
                )

    def get_item(self, item_id: str) -> Optional[ItemRow]:
        with self.connect() as conn:
            r = conn.execute("SELECT * FROM items WHERE item_id=?", (item_id,)).fetchone()
        return self._item_row(r) if r else None

    def list_items(self, limit: Optional[int] = None) -> list[ItemRow]:
        sql = "SELECT * FROM items ORDER BY synced_at DESC, added_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._item_row(r) for r in rows]

    def list_assets(
        self,
        item_id: Optional[str] = None,
        status: Optional[str] = None,
        kinds: Optional[list[str]] = None,
    ) -> list[AssetRow]:
        clauses: list[str] = []
        params: list[Any] = []
        if item_id:
            clauses.append("item_id=?")
            params.append(item_id)
        if status:
            clauses.append("download_status=?")
            params.append(status)
        if kinds:
            kind_clauses: list[str] = []
            for k in kinds:
                if k == "character":
                    kind_clauses.append("(kind = 'character' OR kind LIKE 'character_%')")
                else:
                    kind_clauses.append("kind = ?")
                    params.append(k)
            clauses.append("(" + " OR ".join(kind_clauses) + ")")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM assets{where} ORDER BY item_id, kind", params
            ).fetchall()
        return [self._asset_row(r) for r in rows]

    def claim_assets(
        self,
        *,
        item_ids: Optional[list[str]] = None,
        kinds: Optional[list[str]] = None,
        include_failed: bool = False,
        limit: Optional[int] = None,
    ) -> list[AssetRow]:
        statuses = ["pending"]
        if include_failed:
            statuses.append("failed")
        status_ph = ",".join("?" * len(statuses))
        clauses = [f"download_status IN ({status_ph})", "url != ''"]
        params: list[Any] = list(statuses)
        if item_ids:
            ph = ",".join("?" * len(item_ids))
            clauses.append(f"item_id IN ({ph})")
            params.extend(item_ids)
        if kinds:
            # "character" matches character, character_2, character_3, ...
            kind_clauses: list[str] = []
            for k in kinds:
                if k == "character":
                    kind_clauses.append("(kind = 'character' OR kind LIKE 'character_%')")
                else:
                    kind_clauses.append("kind = ?")
                    params.append(k)
            clauses.append("(" + " OR ".join(kind_clauses) + ")")
        sql = f"SELECT * FROM assets WHERE {' AND '.join(clauses)} ORDER BY item_id, kind"
        if limit:
            sql += f" LIMIT {int(limit)}"

        claimed: list[AssetRow] = []
        now = utc_now()
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            for r in rows:
                conn.execute(
                    """
                    UPDATE assets SET download_status='queued', updated_at=?
                    WHERE asset_id=? AND download_status IN ('pending','failed')
                    """,
                    (now, r["asset_id"]),
                )
                if conn.total_changes:
                    claimed.append(self._asset_row(r))
                    claimed[-1].download_status = "queued"
        return claimed

    def mark_downloading(self, asset_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE assets SET download_status='downloading', updated_at=? WHERE asset_id=?",
                (utc_now(), asset_id),
            )

    def mark_done(self, asset_id: str, file_path: str, file_size: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE assets SET download_status='done', file_path=?, file_size=?,
                    error=NULL, updated_at=?
                WHERE asset_id=?
                """,
                (file_path, file_size, utc_now(), asset_id),
            )

    def mark_failed(self, asset_id: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE assets SET download_status='failed', error=?,
                    attempts=attempts+1, updated_at=?
                WHERE asset_id=?
                """,
                (error[:500], utc_now(), asset_id),
            )

    def retry_failed(self, item_id: Optional[str] = None) -> int:
        with self.connect() as conn:
            if item_id:
                cur = conn.execute(
                    "UPDATE assets SET download_status='pending', error=NULL, updated_at=? "
                    "WHERE download_status='failed' AND item_id=?",
                    (utc_now(), item_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE assets SET download_status='pending', error=NULL, updated_at=? "
                    "WHERE download_status='failed'",
                    (utc_now(),),
                )
            return cur.rowcount

    def reset_stuck(self) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE assets SET download_status='pending', updated_at=? "
                "WHERE download_status IN ('queued','downloading')",
                (utc_now(),),
            )
            return cur.rowcount

    def stats(self, item_id: Optional[str] = None) -> dict[str, int]:
        clauses = []
        params: list[Any] = []
        if item_id:
            clauses.append("item_id=?")
            params.append(item_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT download_status, COUNT(*) AS c FROM assets{where} GROUP BY download_status",
                params,
            ).fetchall()
            item_count = conn.execute(
                "SELECT COUNT(*) AS c FROM items" + (" WHERE item_id=?" if item_id else ""),
                params if item_id else [],
            ).fetchone()["c"]
        out = {r["download_status"]: r["c"] for r in rows}
        out["items"] = item_count
        return out

    def remove_item(self, item_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM items WHERE item_id=?", (item_id,))
            return cur.rowcount > 0

    def update_asset_url(self, asset_id: str, url: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE assets SET url=?, updated_at=? WHERE asset_id=?",
                (url, utc_now(), asset_id),
            )

    def refresh_item_urls(self, item: CollectionItem) -> None:
        """Update stored URLs after re-resolve (e.g. expired signed links)."""
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE items SET character_image=?, cover_image=?, video_url=?,
                    tags_json=?, keywords_json=?, synced_at=?
                WHERE item_id=?
                """,
                (
                    item.character_image,
                    item.cover_image,
                    item.best_video,
                    json.dumps(item.tags, ensure_ascii=False),
                    json.dumps(item.keywords, ensure_ascii=False),
                    now,
                    item.item_id,
                ),
            )
            # re-sync all media kinds (incl. character_2...)
            self._sync_assets(conn, item)

    @staticmethod
    def _item_row(r: sqlite3.Row) -> ItemRow:
        keys = set(r.keys())

        def g(col: str, default: Any = "") -> Any:
            return r[col] if col in keys else default

        return ItemRow(
            item_id=r["item_id"],
            name=r["name"],
            username=r["username"],
            user_id=r["user_id"],
            character_image=r["character_image"],
            cover_image=r["cover_image"],
            video_url=r["video_url"],
            tags_json=r["tags_json"],
            keywords_json=r["keywords_json"],
            categories_json=r["categories_json"],
            model_type=r["model_type"],
            model_id=str(g("model_id", "")),
            model_name=str(g("model_name", "")),
            template_name=str(g("template_name", "")),
            template_creator=str(g("template_creator", "")),
            description=str(g("description", "")),
            custom_prompt=str(g("custom_prompt", "")),
            page_url=r["page_url"],
            dir_name=r["dir_name"],
            parent_id=str(g("parent_id", "")),
            gallery_index=int(g("gallery_index", 0) or 0),
            status=r["status"],
            added_at=r["added_at"],
            synced_at=r["synced_at"],
            note=r["note"],
        )

    @staticmethod
    def _asset_row(r: sqlite3.Row) -> AssetRow:
        return AssetRow(
            asset_id=r["asset_id"],
            item_id=r["item_id"],
            kind=r["kind"],
            url=r["url"],
            download_status=r["download_status"],
            file_path=r["file_path"],
            file_size=r["file_size"],
            error=r["error"],
            attempts=r["attempts"],
            updated_at=r["updated_at"],
        )
