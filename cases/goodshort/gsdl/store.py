"""SQLite library for batch management."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


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
class DramaRow:
    book_id: str
    book_name: str
    book_resource_url: str
    chapter_count: int
    cover: str
    introduction: str
    language: str
    write_status: str
    preview_chapter_num: int
    dir_name: str
    added_at: str
    synced_at: Optional[str]
    note: str


@dataclass
class EpisodeRow:
    chapter_id: str
    book_id: str
    chapter_name: str
    ep_index: int
    chapter_resource_url: str
    price: int
    play_time: int
    m3u8_path: Optional[str]
    download_status: str  # pending|queued|downloading|done|failed|locked|skipped
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
                CREATE TABLE IF NOT EXISTS dramas (
                    book_id TEXT PRIMARY KEY,
                    book_name TEXT NOT NULL,
                    book_resource_url TEXT NOT NULL DEFAULT '',
                    chapter_count INTEGER NOT NULL DEFAULT 0,
                    cover TEXT NOT NULL DEFAULT '',
                    introduction TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL DEFAULT '',
                    write_status TEXT NOT NULL DEFAULT '',
                    preview_chapter_num INTEGER NOT NULL DEFAULT 0,
                    dir_name TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    synced_at TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS episodes (
                    chapter_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    chapter_name TEXT NOT NULL,
                    ep_index INTEGER NOT NULL DEFAULT 0,
                    chapter_resource_url TEXT NOT NULL DEFAULT '',
                    price INTEGER NOT NULL DEFAULT 0,
                    play_time INTEGER NOT NULL DEFAULT 0,
                    m3u8_path TEXT,
                    download_status TEXT NOT NULL DEFAULT 'pending',
                    file_path TEXT,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(book_id) REFERENCES dramas(book_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_episodes_book
                    ON episodes(book_id, ep_index);
                CREATE INDEX IF NOT EXISTS idx_episodes_status
                    ON episodes(download_status);
                """
            )

    def upsert_drama(self, meta: Any, note: str = "") -> DramaRow:
        now = utc_now()
        dir_name = f"{safe_name(meta.book_name)}_{meta.book_id}"
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT dir_name, added_at, note FROM dramas WHERE book_id = ?",
                (meta.book_id,),
            ).fetchone()
            if existing:
                dir_name = existing["dir_name"]
                added_at = existing["added_at"]
                note = note or existing["note"]
            else:
                added_at = now
            conn.execute(
                """
                INSERT INTO dramas (
                    book_id, book_name, book_resource_url, chapter_count, cover,
                    introduction, language, write_status, preview_chapter_num,
                    dir_name, added_at, synced_at, note, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(book_id) DO UPDATE SET
                    book_name=excluded.book_name,
                    book_resource_url=excluded.book_resource_url,
                    chapter_count=excluded.chapter_count,
                    cover=excluded.cover,
                    introduction=excluded.introduction,
                    language=excluded.language,
                    write_status=excluded.write_status,
                    preview_chapter_num=excluded.preview_chapter_num,
                    synced_at=excluded.synced_at,
                    note=CASE WHEN excluded.note != '' THEN excluded.note ELSE dramas.note END,
                    raw_json=excluded.raw_json
                """,
                (
                    meta.book_id,
                    meta.book_name,
                    meta.book_resource_url,
                    meta.chapter_count,
                    meta.cover,
                    meta.introduction,
                    meta.language,
                    meta.write_status,
                    meta.preview_chapter_num,
                    dir_name,
                    added_at,
                    now,
                    note,
                    json.dumps(getattr(meta, "raw", {}), ensure_ascii=False),
                ),
            )
        row = self.get_drama(meta.book_id)
        assert row is not None
        return row

    def replace_episodes(self, book_id: str, chapters: list[Any]) -> int:
        now = utc_now()
        with self.connect() as conn:
            existing = {
                r["chapter_id"]: r
                for r in conn.execute(
                    "SELECT * FROM episodes WHERE book_id = ?", (book_id,)
                )
            }
            keep_ids = set()
            for ch in chapters:
                keep_ids.add(ch.chapter_id)
                old = existing.get(ch.chapter_id)
                if ch.m3u8_path:
                    status = (old["download_status"] if old else "pending")
                    if status == "locked":
                        status = "pending"
                else:
                    # paid / no stream
                    if old and old["download_status"] == "done":
                        status = "done"
                    else:
                        status = "locked" if int(ch.price or 0) > 0 else "pending"

                # Preserve done/downloading carefully
                if old and old["download_status"] in ("done", "downloading", "queued"):
                    if old["download_status"] == "done":
                        status = "done"
                    elif old["download_status"] in ("downloading", "queued") and ch.m3u8_path:
                        status = old["download_status"]

                conn.execute(
                    """
                    INSERT INTO episodes (
                        chapter_id, book_id, chapter_name, ep_index, chapter_resource_url,
                        price, play_time, m3u8_path, download_status, file_path, file_size,
                        error, attempts, updated_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chapter_id) DO UPDATE SET
                        chapter_name=excluded.chapter_name,
                        ep_index=excluded.ep_index,
                        chapter_resource_url=excluded.chapter_resource_url,
                        price=excluded.price,
                        play_time=excluded.play_time,
                        m3u8_path=excluded.m3u8_path,
                        download_status=excluded.download_status,
                        updated_at=excluded.updated_at,
                        raw_json=excluded.raw_json
                    """,
                    (
                        ch.chapter_id,
                        book_id,
                        ch.chapter_name,
                        ch.index,
                        ch.chapter_resource_url,
                        int(ch.price or 0),
                        int(ch.play_time or 0),
                        ch.m3u8_path,
                        status,
                        old["file_path"] if old else None,
                        int(old["file_size"]) if old else 0,
                        old["error"] if old else None,
                        int(old["attempts"]) if old else 0,
                        now,
                        json.dumps(getattr(ch, "raw", {}), ensure_ascii=False),
                    ),
                )
            # Remove episodes no longer present
            if keep_ids:
                placeholders = ",".join("?" for _ in keep_ids)
                conn.execute(
                    f"DELETE FROM episodes WHERE book_id = ? AND chapter_id NOT IN ({placeholders})",
                    (book_id, *keep_ids),
                )
            conn.execute(
                "UPDATE dramas SET synced_at = ?, chapter_count = ? WHERE book_id = ?",
                (now, len(keep_ids) or len(chapters), book_id),
            )
        return len(chapters)

    def get_drama(self, book_id: str) -> Optional[DramaRow]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM dramas WHERE book_id = ?", (book_id,)
            ).fetchone()
        return self._drama_from_row(row) if row else None

    def list_dramas(self) -> list[DramaRow]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM dramas ORDER BY added_at DESC"
            ).fetchall()
        return [self._drama_from_row(r) for r in rows]

    def remove_drama(self, book_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM dramas WHERE book_id = ?", (book_id,))
            return cur.rowcount > 0

    def list_episodes(
        self,
        book_id: str,
        statuses: Optional[list[str]] = None,
    ) -> list[EpisodeRow]:
        q = "SELECT * FROM episodes WHERE book_id = ?"
        args: list[Any] = [book_id]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            q += f" AND download_status IN ({placeholders})"
            args.extend(statuses)
        q += " ORDER BY ep_index ASC, chapter_name ASC"
        with self.connect() as conn:
            rows = conn.execute(q, args).fetchall()
        return [self._episode_from_row(r) for r in rows]

    def claim_download_batch(
        self,
        book_ids: Optional[list[str]],
        free_only: bool,
        include_failed: bool,
        limit: Optional[int],
        ep_from: Optional[int],
        ep_to: Optional[int],
    ) -> list[EpisodeRow]:
        clauses = ["download_status IN ('pending', 'failed')"]
        args: list[Any] = []
        if not include_failed:
            clauses = ["download_status = 'pending'"]
        if free_only:
            clauses.append("price = 0")
            clauses.append("m3u8_path IS NOT NULL")
        else:
            # still need a stream URL eventually; skip known locked
            clauses.append("download_status != 'locked'")
        if book_ids:
            placeholders = ",".join("?" for _ in book_ids)
            clauses.append(f"book_id IN ({placeholders})")
            args.extend(book_ids)
        if ep_from is not None:
            clauses.append("ep_index >= ?")
            args.append(ep_from)
        if ep_to is not None:
            clauses.append("ep_index <= ?")
            args.append(ep_to)

        where = " AND ".join(clauses)
        sql = f"SELECT * FROM episodes WHERE {where} ORDER BY book_id, ep_index"
        if limit:
            sql += f" LIMIT {int(limit)}"

        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
            now = utc_now()
            claimed: list[EpisodeRow] = []
            for row in rows:
                cur = conn.execute(
                    """
                    UPDATE episodes
                    SET download_status = 'queued', updated_at = ?, error = NULL
                    WHERE chapter_id = ? AND download_status IN ('pending', 'failed')
                    """,
                    (now, row["chapter_id"]),
                )
                if cur.rowcount:
                    ep = self._episode_from_row(row)
                    ep.download_status = "queued"
                    claimed.append(ep)
        return claimed

    def mark_episode(
        self,
        chapter_id: str,
        status: str,
        *,
        file_path: Optional[str] = None,
        file_size: int = 0,
        error: Optional[str] = None,
        inc_attempt: bool = False,
        m3u8_path: Optional[str] = None,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM episodes WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchone()
            attempts = int(row["attempts"]) if row else 0
            if inc_attempt:
                attempts += 1
            conn.execute(
                """
                UPDATE episodes SET
                    download_status = ?,
                    file_path = COALESCE(?, file_path),
                    file_size = CASE WHEN ? > 0 THEN ? ELSE file_size END,
                    error = ?,
                    attempts = ?,
                    m3u8_path = COALESCE(?, m3u8_path),
                    updated_at = ?
                WHERE chapter_id = ?
                """,
                (
                    status,
                    file_path,
                    file_size,
                    file_size,
                    error,
                    attempts,
                    m3u8_path,
                    now,
                    chapter_id,
                ),
            )

    def reset_stuck(self) -> int:
        """Reset downloading/queued to pending after crash."""
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE episodes
                SET download_status = 'pending', updated_at = ?
                WHERE download_status IN ('downloading', 'queued')
                """,
                (utc_now(),),
            )
            return cur.rowcount

    def retry_failed(self, book_id: Optional[str] = None) -> int:
        with self.connect() as conn:
            if book_id:
                cur = conn.execute(
                    """
                    UPDATE episodes
                    SET download_status = 'pending', error = NULL, updated_at = ?
                    WHERE download_status = 'failed' AND book_id = ?
                    """,
                    (utc_now(), book_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE episodes
                    SET download_status = 'pending', error = NULL, updated_at = ?
                    WHERE download_status = 'failed'
                    """,
                    (utc_now(),),
                )
            return cur.rowcount

    def stats(self, book_id: Optional[str] = None) -> dict[str, int]:
        with self.connect() as conn:
            if book_id:
                rows = conn.execute(
                    """
                    SELECT download_status, COUNT(*) AS c
                    FROM episodes WHERE book_id = ?
                    GROUP BY download_status
                    """,
                    (book_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT download_status, COUNT(*) AS c
                    FROM episodes
                    GROUP BY download_status
                    """
                ).fetchall()
        out = {r["download_status"]: int(r["c"]) for r in rows}
        out["total"] = sum(v for k, v in out.items() if k != "total")
        return out

    def drama_progress(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            dramas = conn.execute(
                "SELECT * FROM dramas ORDER BY added_at DESC"
            ).fetchall()
            result = []
            for d in dramas:
                stats_rows = conn.execute(
                    """
                    SELECT download_status, COUNT(*) AS c
                    FROM episodes WHERE book_id = ?
                    GROUP BY download_status
                    """,
                    (d["book_id"],),
                ).fetchall()
                stats = {r["download_status"]: int(r["c"]) for r in stats_rows}
                result.append(
                    {
                        "book_id": d["book_id"],
                        "book_name": d["book_name"],
                        "chapter_count": d["chapter_count"],
                        "dir_name": d["dir_name"],
                        "synced_at": d["synced_at"],
                        "stats": stats,
                        "done": stats.get("done", 0),
                        "failed": stats.get("failed", 0),
                        "locked": stats.get("locked", 0),
                        "pending": stats.get("pending", 0) + stats.get("queued", 0),
                    }
                )
        return result

    @staticmethod
    def _drama_from_row(row: sqlite3.Row) -> DramaRow:
        return DramaRow(
            book_id=row["book_id"],
            book_name=row["book_name"],
            book_resource_url=row["book_resource_url"],
            chapter_count=row["chapter_count"],
            cover=row["cover"],
            introduction=row["introduction"],
            language=row["language"],
            write_status=row["write_status"],
            preview_chapter_num=row["preview_chapter_num"],
            dir_name=row["dir_name"],
            added_at=row["added_at"],
            synced_at=row["synced_at"],
            note=row["note"],
        )

    @staticmethod
    def _episode_from_row(row: sqlite3.Row) -> EpisodeRow:
        return EpisodeRow(
            chapter_id=row["chapter_id"],
            book_id=row["book_id"],
            chapter_name=row["chapter_name"],
            ep_index=row["ep_index"],
            chapter_resource_url=row["chapter_resource_url"],
            price=row["price"],
            play_time=row["play_time"],
            m3u8_path=row["m3u8_path"],
            download_status=row["download_status"],
            file_path=row["file_path"],
            file_size=row["file_size"],
            error=row["error"],
            attempts=row["attempts"],
            updated_at=row["updated_at"],
        )
