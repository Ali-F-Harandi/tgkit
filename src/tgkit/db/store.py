"""DB store — CRUD operations for all tables.

Each table has a Store class with typed methods.
All methods use the Database context manager (thread-safe).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from tgkit.db.connection import Database

logger = logging.getLogger(__name__)


# ============================================================================
# Channel Store
# ============================================================================

class ChannelStore:
    """CRUD for the channels table."""

    def __init__(self, db: Database):
        self.db = db

    def upsert(self, channel_id: int, username: str | None = None,
               access_hash: int | None = None, title: str | None = None,
               ch_type: str | None = None, role: str = "source",
               note: str | None = None) -> None:
        """Insert or update a channel."""
        with self.db.connect() as conn:
            conn.execute("""
                INSERT INTO channels (id, username, access_hash, title, type, role, added_at, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    username = COALESCE(excluded.username, username),
                    access_hash = COALESCE(excluded.access_hash, access_hash),
                    title = COALESCE(excluded.title, title),
                    type = COALESCE(excluded.type, type),
                    role = COALESCE(excluded.role, role),
                    note = COALESCE(excluded.note, note)
            """, (channel_id, username, access_hash, title, ch_type, role,
                  int(time.time()), note))

    def get(self, channel_id: int) -> dict[str, Any] | None:
        """Get a channel by ID."""
        return self.db.query_one("SELECT * FROM channels WHERE id = ?", (channel_id,))

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        """Get a channel by username (without @)."""
        return self.db.query_one("SELECT * FROM channels WHERE username = ?", (username,))

    def list_all(self) -> list[dict[str, Any]]:
        """List all channels."""
        return self.db.query_all("SELECT * FROM channels ORDER BY role, added_at")

    def list_by_role(self, role: str) -> list[dict[str, Any]]:
        """List channels by role."""
        return self.db.query_all("SELECT * FROM channels WHERE role = ? ORDER BY added_at", (role,))

    def update_scan_info(self, channel_id: int, last_message_id: int) -> None:
        """Update last_scanned_at and last_message_id."""
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE channels SET last_scanned_at = ?, last_message_id = ? WHERE id = ?",
                (int(time.time()), last_message_id, channel_id),
            )

    def remove(self, channel_id: int) -> bool:
        """Remove a channel. Returns True if deleted."""
        with self.db.connect() as conn:
            cur = conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
            return cur.rowcount > 0


# ============================================================================
# Bot Store (runtime stats — tokens stay in config)
# ============================================================================

class BotStore:
    """CRUD for the bots table (runtime stats only)."""

    def __init__(self, db: Database):
        self.db = db

    def upsert(self, bot_id: int, username: str | None = None,
               first_name: str | None = None) -> None:
        """Insert or update a bot's info."""
        with self.db.connect() as conn:
            conn.execute("""
                INSERT INTO bots (bot_id, username, first_name, last_used_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(bot_id) DO UPDATE SET
                    username = COALESCE(excluded.username, username),
                    first_name = COALESCE(excluded.first_name, first_name),
                    last_used_at = excluded.last_used_at
            """, (bot_id, username, first_name, int(time.time())))

    def update_stats(self, bot_id: int, request_count: int,
                     floodwait_count: int, last_delay: float) -> None:
        """Update a bot's runtime stats."""
        with self.db.connect() as conn:
            conn.execute("""
                UPDATE bots SET
                    request_count = ?,
                    floodwait_count = ?,
                    last_delay = ?,
                    last_used_at = ?
                WHERE bot_id = ?
            """, (request_count, floodwait_count, last_delay, int(time.time()), bot_id))

    def get(self, bot_id: int) -> dict[str, Any] | None:
        return self.db.query_one("SELECT * FROM bots WHERE bot_id = ?", (bot_id,))

    def list_all(self) -> list[dict[str, Any]]:
        return self.db.query_all("SELECT * FROM bots ORDER BY bot_id")


# ============================================================================
# Scan Store
# ============================================================================

class ScanStore:
    """CRUD for the scans table (scan runs + resume state)."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, channel_id: int, start_id: int, end_id: int,
               config_json: str | None = None) -> int:
        """Create a new scan run. Returns scan_id."""
        with self.db.connect() as conn:
            cur = conn.execute("""
                INSERT INTO scans (channel_id, start_id, end_id, status, started_at, config_json)
                VALUES (?, ?, ?, 'running', ?, ?)
            """, (channel_id, start_id, end_id, int(time.time()), config_json))
            return cur.lastrowid

    def update_progress(self, scan_id: int, found_count: int,
                        deleted_count: int, last_completed_id: int) -> None:
        """Update scan progress (for resume)."""
        with self.db.connect() as conn:
            conn.execute("""
                UPDATE scans SET
                    found_count = ?,
                    deleted_count = ?,
                    last_completed_id = ?
                WHERE id = ?
            """, (found_count, deleted_count, last_completed_id, scan_id))

    def complete(self, scan_id: int, found_count: int, deleted_count: int,
                 csv_path: str | None = None) -> None:
        """Mark a scan as completed."""
        with self.db.connect() as conn:
            conn.execute("""
                UPDATE scans SET
                    status = 'completed',
                    found_count = ?,
                    deleted_count = ?,
                    finished_at = ?,
                    csv_path = ?
                WHERE id = ?
            """, (found_count, deleted_count, int(time.time()), csv_path, scan_id))

    def mark_interrupted(self, scan_id: int) -> None:
        """Mark a scan as interrupted (for resume later)."""
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE scans SET status = 'interrupted' WHERE id = ?",
                (scan_id,),
            )

    def get(self, scan_id: int) -> dict[str, Any] | None:
        return self.db.query_one("SELECT * FROM scans WHERE id = ?", (scan_id,))

    def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT * FROM scans ORDER BY started_at DESC LIMIT ?", (limit,)
        )


# ============================================================================
# Message Store (scan results)
# ============================================================================

class MessageStore:
    """CRUD for the messages table (scanned message metadata)."""

    def __init__(self, db: Database):
        self.db = db

    def insert_batch(self, scan_id: int, channel_id: int,
                     messages: list[dict[str, Any]]) -> int:
        """Insert a batch of messages. Returns count inserted.

        Uses INSERT OR IGNORE to skip duplicates (UNIQUE on channel_id+msg_id).
        """
        if not messages:
            return 0

        rows = []
        for m in messages:
            rows.append((
                scan_id,
                channel_id,
                m.get("msg_id", 0),
                m.get("date", ""),
                m.get("media_type", ""),
                m.get("file_name", ""),
                m.get("file_extension", ""),
                int(m.get("file_size", 0) or 0),
                m.get("mime_type", ""),
                int(m.get("duration", 0) or 0),
                int(m.get("width", 0) or 0),
                int(m.get("height", 0) or 0),
                m.get("caption", ""),
                1 if m.get("is_marker") else 0,
                m.get("marker_emoji", ""),
                1 if m.get("has_thumb") else 0,
                m.get("file_id", ""),
                m.get("message_link", ""),
            ))

        with self.db.connect() as conn:
            conn.executemany("""
                INSERT OR IGNORE INTO messages (
                    scan_id, channel_id, msg_id, date, media_type,
                    file_name, file_extension, file_size, mime_type,
                    duration, width, height, caption,
                    is_marker, marker_emoji, has_thumb, file_id, message_link
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)

        return len(rows)

    def query_by_scan(self, scan_id: int,
                      filters: dict[str, Any] | None = None,
                      limit: int | None = None,
                      offset: int = 0) -> list[dict[str, Any]]:
        """Query messages from a scan, with optional filters.

        Filters:
            - media_type: str (e.g., "document")
            - file_extension: str (e.g., ".cbz")
            - min_size: int (bytes)
            - max_size: int (bytes)
            - is_marker: bool
        """
        sql = "SELECT * FROM messages WHERE scan_id = ?"
        params: list[Any] = [scan_id]

        if filters:
            if "media_type" in filters:
                sql += " AND media_type = ?"
                params.append(filters["media_type"])
            if "file_extension" in filters:
                sql += " AND file_extension = ?"
                params.append(filters["file_extension"])
            if "min_size" in filters:
                sql += " AND file_size >= ?"
                params.append(int(filters["min_size"]))
            if "max_size" in filters:
                sql += " AND file_size <= ?"
                params.append(int(filters["max_size"]))
            if "is_marker" in filters:
                sql += " AND is_marker = ?"
                params.append(1 if filters["is_marker"] else 0)

        sql += " ORDER BY msg_id ASC"
        if limit:
            sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"

        return self.db.query_all(sql, tuple(params))

    def count_by_scan(self, scan_id: int) -> int:
        """Count messages in a scan."""
        result = self.db.query_one(
            "SELECT COUNT(*) as count FROM messages WHERE scan_id = ?", (scan_id,)
        )
        return result["count"] if result else 0

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Full-text search on file_name + caption."""
        return self.db.query_all(
            """SELECT m.* FROM messages m
               JOIN messages_fts fts ON m.id = fts.rowid
               WHERE messages_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit)
        )

    def delete_by_scan(self, scan_id: int) -> int:
        """Delete all messages from a scan. Returns count deleted."""
        with self.db.connect() as conn:
            cur = conn.execute("DELETE FROM messages WHERE scan_id = ?", (scan_id,))
            return cur.rowcount


# ============================================================================
# Operations Log Store
# ============================================================================

class OperationsLogStore:
    """Append-only audit trail for all operations."""

    def __init__(self, db: Database):
        self.db = db

    def log(self, op_type: str, status: str,
            source_channel: int | None = None,
            source_msg_id: int | None = None,
            dest_channel: int | None = None,
            dest_msg_id: int | None = None,
            file_id: int | None = None,
            bot_id: int | None = None,
            error: str | None = None,
            meta_json: str | None = None) -> int:
        """Log an operation. Returns log entry ID."""
        with self.db.connect() as conn:
            cur = conn.execute("""
                INSERT INTO operations_log (
                    op_type, status, source_channel, source_msg_id,
                    dest_channel, dest_msg_id, file_id, bot_id,
                    error, started_at, finished_at, meta_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                op_type, status, source_channel, source_msg_id,
                dest_channel, dest_msg_id, file_id, bot_id,
                error, int(time.time()), int(time.time()), meta_json
            ))
            return cur.lastrowid

    def list_recent(self, op_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """List recent operations, optionally filtered by type."""
        if op_type:
            return self.db.query_all(
                "SELECT * FROM operations_log WHERE op_type = ? ORDER BY started_at DESC LIMIT ?",
                (op_type, limit)
            )
        return self.db.query_all(
            "SELECT * FROM operations_log ORDER BY started_at DESC LIMIT ?", (limit,)
        )

    def stats(self) -> dict[str, Any]:
        """Get operation stats by type and status."""
        rows = self.db.query_all("""
            SELECT op_type, status, COUNT(*) as count
            FROM operations_log
            GROUP BY op_type, status
            ORDER BY op_type, status
        """)
        return {"breakdown": rows}


# ============================================================================
# Library Store (vault files + single-file copies)
# ============================================================================

class LibraryStore:
    """CRUD for the library table (vault files + copied files)."""

    def __init__(self, db: Database):
        self.db = db

    def insert(self, name: str, size: int, sha256: str,
               total_parts: int, chunk_size: int, message_ids: list[int],
               main_channel: int, share_link: str,
               kind: str = "copied",
               manifest_msg_id: int | None = None,
               description: str | None = None,
               caption: str | None = None,
               file_extension: str | None = None,
               mime_type: str | None = None) -> int:
        """Insert a library entry. Returns library ID.

        If sha256 already exists, updates the existing entry instead.
        """
        with self.db.connect() as conn:
            # Check if sha256 exists
            existing = conn.execute(
                "SELECT id FROM library WHERE sha256 = ?", (sha256,)
            ).fetchone()

            if existing:
                # Update existing entry
                conn.execute("""
                    UPDATE library SET
                        name = ?, size = ?, total_parts = ?, chunk_size = ?,
                        message_ids = ?, manifest_msg_id = ?, description = ?,
                        main_channel = ?, share_link = ?,
                        uploaded_at = ?, status = 'uploaded', kind = ?
                    WHERE id = ?
                """, (
                    name, size, total_parts, chunk_size,
                    json.dumps(message_ids), manifest_msg_id, description,
                    main_channel, share_link,
                    int(time.time()), kind,
                    existing[0]
                ))
                return existing[0]
            else:
                cur = conn.execute("""
                    INSERT INTO library (
                        name, size, sha256, total_parts, chunk_size,
                        message_ids, manifest_msg_id, description, caption,
                        file_extension, mime_type,
                        main_channel, share_link, session_id,
                        uploaded_at, status, kind
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'uploaded', ?)
                """, (
                    name, size, sha256, total_parts, chunk_size,
                    json.dumps(message_ids), manifest_msg_id, description, caption,
                    file_extension, mime_type,
                    main_channel, share_link, None,
                    int(time.time()), kind
                ))
                return cur.lastrowid

    def get(self, library_id: int) -> dict[str, Any] | None:
        return self.db.query_one("SELECT * FROM library WHERE id = ?", (library_id,))

    def get_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        return self.db.query_one("SELECT * FROM library WHERE sha256 = ?", (sha256,))

    def list_recent(self, limit: int = 20, kind: str | None = None) -> list[dict[str, Any]]:
        """List recent library entries."""
        if kind:
            return self.db.query_all(
                "SELECT * FROM library WHERE kind = ? ORDER BY uploaded_at DESC LIMIT ?",
                (kind, limit)
            )
        return self.db.query_all(
            "SELECT * FROM library ORDER BY uploaded_at DESC LIMIT ?", (limit,)
        )

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search library by name."""
        return self.db.query_all(
            "SELECT * FROM library WHERE name LIKE ? ORDER BY uploaded_at DESC LIMIT ?",
            (f"%{query}%", limit)
        )

    def update_status(self, library_id: int, status: str) -> None:
        """Update a library entry's status (uploaded/deleted/corrupted)."""
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE library SET status = ? WHERE id = ?", (status, library_id)
            )


# ============================================================================
# Bot Scan State Store (per-bot resume state for ParallelRangeScanner)
# ============================================================================

class BotScanStateStore:
    """CRUD for the bot_scan_state table.

    Tracks each bot's progress independently in a parallel-range scan,
    so an interrupted scan can be resumed per-bot.
    """

    def __init__(self, db: Database):
        self.db = db

    def init_for_scan(
        self, scan_id: int,
        assignments: list[dict[str, Any]],
    ) -> None:
        """Initialize resume rows for each bot in a parallel-range scan.

        Args:
            scan_id: Scan ID
            assignments: list of dicts with keys:
                - bot_idx: int
                - bot_username: str
                - range_start: int (highest ID)
                - range_end: int (lowest ID)
        """
        now = int(time.time())
        rows = [
            (
                scan_id,
                a["bot_idx"],
                a["bot_username"],
                a["range_start"],
                a["range_end"],
                a["range_start"] + 1,  # last_completed_id (start fresh)
                0, 0, "pending", now, None, now,
            )
            for a in assignments
        ]
        with self.db.connect() as conn:
            conn.executemany("""
                INSERT OR IGNORE INTO bot_scan_state (
                    scan_id, bot_idx, bot_username, range_start, range_end,
                    last_completed_id, found_count, deleted_count,
                    status, started_at, finished_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)

    def get(self, scan_id: int, bot_idx: int) -> dict[str, Any] | None:
        return self.db.query_one(
            "SELECT * FROM bot_scan_state WHERE scan_id = ? AND bot_idx = ?",
            (scan_id, bot_idx),
        )

    def list_for_scan(self, scan_id: int) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT * FROM bot_scan_state WHERE scan_id = ? ORDER BY bot_idx",
            (scan_id,),
        )

    def update_progress(
        self, scan_id: int, bot_idx: int,
        last_completed_id: int, found_count: int, deleted_count: int,
        status: str = "running",
    ) -> None:
        """Update one bot's resume state."""
        with self.db.connect() as conn:
            conn.execute("""
                UPDATE bot_scan_state SET
                    last_completed_id = ?,
                    found_count = ?,
                    deleted_count = ?,
                    status = ?,
                    updated_at = ?
                WHERE scan_id = ? AND bot_idx = ?
            """, (last_completed_id, found_count, deleted_count,
                  status, int(time.time()), scan_id, bot_idx))

    def mark_completed(
        self, scan_id: int, bot_idx: int,
        found_count: int, deleted_count: int,
    ) -> None:
        """Mark one bot's slice as fully completed."""
        with self.db.connect() as conn:
            conn.execute("""
                UPDATE bot_scan_state SET
                    status = 'completed',
                    found_count = ?,
                    deleted_count = ?,
                    last_completed_id = range_end,
                    finished_at = ?,
                    updated_at = ?
                WHERE scan_id = ? AND bot_idx = ?
            """, (found_count, deleted_count, int(time.time()),
                  int(time.time()), scan_id, bot_idx))

    def all_completed(self, scan_id: int) -> bool:
        """Check if every bot in the scan is completed."""
        rows = self.list_for_scan(scan_id)
        return bool(rows) and all(r["status"] == "completed" for r in rows)


# ============================================================================
# Combined store (convenience)
# ============================================================================

class Store:
    """All stores in one convenient object.

    Usage:
        db = get_db()
        store = Store(db)
        store.channels.upsert(...)
        store.messages.insert_batch(...)
        store.ops.log(...)
    """

    def __init__(self, db: Database):
        self.db = db
        self.channels = ChannelStore(db)
        self.bots = BotStore(db)
        self.scans = ScanStore(db)
        self.messages = MessageStore(db)
        self.ops = OperationsLogStore(db)
        self.library = LibraryStore(db)
        self.bot_scan_state = BotScanStateStore(db)
