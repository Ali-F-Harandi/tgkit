"""Database schema — all DDL in one place.

Tables (Phase 0 — foundational; Phase 1+ adds scan/results/operations):

    channels         — peer cache + role assignments
    bots             — runtime stats (tokens stay in config, NOT here)

Future tables (Phase 1+):
    scans, messages, library, chunks, tags, downloads, operations_log, orphans
"""

from __future__ import annotations

# ============================================================================
# Phase 0 schema — channels + bots
# ============================================================================

SCHEMA_SQL = """
-- ============================================================================
-- channels: peer cache + role assignments
-- ============================================================================
CREATE TABLE IF NOT EXISTS channels (
    id               INTEGER PRIMARY KEY,      -- -100... form (or hash of username)
    username         TEXT,                     -- @username (NULL for private)
    access_hash      INTEGER,                  -- cached MTProto access_hash
    title            TEXT,
    type             TEXT,                     -- 'public'|'private'|'supergroup'|'chat'
    role             TEXT DEFAULT 'source',    -- 'source'|'destination'|'vault_main'|'vault_temp'|'vault_storage'|'sync'
    added_at         INTEGER NOT NULL,
    last_scanned_at  INTEGER,
    last_message_id  INTEGER,
    note             TEXT
);

-- ============================================================================
-- bots: runtime stats (tokens live in config.json, NOT here)
-- ============================================================================
CREATE TABLE IF NOT EXISTS bots (
    bot_id           INTEGER PRIMARY KEY,
    username         TEXT,
    first_name       TEXT,
    request_count    INTEGER DEFAULT 0,
    floodwait_count  INTEGER DEFAULT 0,
    last_delay       REAL DEFAULT 0,
    last_used_at     INTEGER,
    status           TEXT DEFAULT 'active'     -- active|disabled|invalid
);

-- ============================================================================
-- Phase 1+ tables (defined now so migrations are additive)
-- ============================================================================

-- scans: one row per scan execution (backs resume)
CREATE TABLE IF NOT EXISTS scans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id        INTEGER NOT NULL,
    start_id          INTEGER NOT NULL,
    end_id            INTEGER NOT NULL,
    found_count       INTEGER DEFAULT 0,
    deleted_count     INTEGER DEFAULT 0,
    last_completed_id INTEGER,
    status            TEXT DEFAULT 'running',  -- running|completed|interrupted|failed
    started_at        INTEGER NOT NULL,
    finished_at       INTEGER,
    csv_path          TEXT,
    config_json       TEXT,
    FOREIGN KEY (channel_id) REFERENCES channels(id)
);

-- messages: scanned message metadata (18 columns, normalized)
CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id          INTEGER NOT NULL,
    channel_id       INTEGER NOT NULL,
    msg_id           INTEGER NOT NULL,
    date             TEXT,
    media_type       TEXT,
    file_name        TEXT,
    file_extension   TEXT,
    file_size        INTEGER,
    mime_type        TEXT,
    duration         INTEGER,
    width            INTEGER,
    height           INTEGER,
    caption          TEXT,
    is_marker        INTEGER DEFAULT 0,
    marker_emoji     TEXT,
    has_thumb        INTEGER DEFAULT 0,
    file_id          TEXT,                     -- ⚠ ephemeral — re-fetch at copy time
    message_link     TEXT,
    UNIQUE(channel_id, msg_id),
    FOREIGN KEY (scan_id) REFERENCES scans(id),
    FOREIGN KEY (channel_id) REFERENCES channels(id)
);
CREATE INDEX IF NOT EXISTS idx_messages_scan        ON messages(scan_id);
CREATE INDEX IF NOT EXISTS idx_messages_channel_msg ON messages(channel_id, msg_id);
CREATE INDEX IF NOT EXISTS idx_messages_media_type  ON messages(media_type);
CREATE INDEX IF NOT EXISTS idx_messages_ext         ON messages(file_extension);

-- Full-text search on file_name + caption
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    file_name, caption, content='messages', content_rowid='id'
);

-- ============================================================================
-- Vault library (Phase 3)
-- ============================================================================
CREATE TABLE IF NOT EXISTS library (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    size              INTEGER NOT NULL,
    sha256            TEXT NOT NULL UNIQUE,
    total_parts       INTEGER NOT NULL,
    chunk_size        INTEGER NOT NULL,
    message_ids       TEXT NOT NULL,           -- JSON array
    manifest_msg_id   INTEGER,
    description_msg_id INTEGER,
    description       TEXT,
    caption           TEXT,                    -- caption used when sending
    file_extension    TEXT,
    mime_type         TEXT,
    main_channel      INTEGER,
    share_link        TEXT,
    session_id        TEXT,
    uploaded_at       INTEGER NOT NULL,
    last_accessed_at  INTEGER,
    status            TEXT DEFAULT 'uploaded',
    encrypted         INTEGER DEFAULT 0,
    compressed        INTEGER DEFAULT 0,
    has_chunk_header  INTEGER DEFAULT 0,
    encryption_salt   TEXT,
    original_size     INTEGER,
    kind              TEXT DEFAULT 'vault',    -- vault(chunked)|single|copied(file_id copy)
    FOREIGN KEY (main_channel) REFERENCES channels(id)
);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    channel_id  INTEGER,
    size        INTEGER,
    created_at  INTEGER NOT NULL,
    UNIQUE(file_id, chunk_index),
    FOREIGN KEY (file_id) REFERENCES library(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id    INTEGER NOT NULL,
    tag        TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(file_id, tag),
    FOREIGN KEY (file_id) REFERENCES library(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS downloads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER,
    output_path     TEXT,
    sha256_verified INTEGER NOT NULL,
    downloaded_at   INTEGER NOT NULL,
    FOREIGN KEY (file_id) REFERENCES library(id)
);

-- ============================================================================
-- operations_log: audit trail for all operations
-- ============================================================================
CREATE TABLE IF NOT EXISTS operations_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    op_type        TEXT NOT NULL,              -- forward|copy|reupload|upload|download|scan|orphan|db_sync
    status         TEXT NOT NULL,              -- ok|failed|partial|interrupted
    source_channel INTEGER,
    source_msg_id  INTEGER,
    dest_channel   INTEGER,
    dest_msg_id    INTEGER,
    file_id        INTEGER,
    bot_id         INTEGER,
    error          TEXT,
    started_at     INTEGER NOT NULL,
    finished_at    INTEGER,
    meta_json      TEXT,
    FOREIGN KEY (source_channel) REFERENCES channels(id),
    FOREIGN KEY (dest_channel) REFERENCES channels(id)
);
CREATE INDEX IF NOT EXISTS idx_oplog_type_time ON operations_log(op_type, started_at);

-- ============================================================================
-- orphans: untracked messages found in channels
-- ============================================================================
CREATE TABLE IF NOT EXISTS orphans (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id            INTEGER NOT NULL,
    msg_id                INTEGER NOT NULL,
    name                  TEXT,
    file_size             INTEGER,
    is_manifest           INTEGER DEFAULT 0,
    message_type          TEXT,
    discovered_at         INTEGER NOT NULL,
    deleted_from_telegram INTEGER DEFAULT 0,
    UNIQUE(channel_id, msg_id),
    FOREIGN KEY (channel_id) REFERENCES channels(id)
);

-- ============================================================================
-- bot_scan_state: per-bot resume state for ParallelRangeScanner
-- ============================================================================
-- Tracks each bot's progress independently so that an interrupted
-- parallel-range scan can be resumed per-bot (the bot that finished
-- won't redo work; the bot that was mid-range continues where it left off).
CREATE TABLE IF NOT EXISTS bot_scan_state (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id             INTEGER NOT NULL,
    bot_idx             INTEGER NOT NULL,
    bot_username        TEXT,
    range_start         INTEGER NOT NULL,      -- highest ID in this bot's slice
    range_end           INTEGER NOT NULL,      -- lowest ID in this bot's slice
    last_completed_id   INTEGER,               -- next scan resumes from (last_completed_id - 1)
    found_count         INTEGER DEFAULT 0,
    deleted_count       INTEGER DEFAULT 0,
    status              TEXT DEFAULT 'pending',-- pending|running|completed|failed
    started_at          INTEGER,
    finished_at         INTEGER,
    updated_at          INTEGER,
    UNIQUE(scan_id, bot_idx),
    FOREIGN KEY (scan_id) REFERENCES scans(id)
);
CREATE INDEX IF NOT EXISTS idx_bot_scan_state_scan ON bot_scan_state(scan_id);
"""


# ============================================================================
# Migrations (additive ALTER TABLE statements for future versions)
# ============================================================================

MIGRATIONS = [
    # (version, description, sql)
    # Example for future:
    # (2, "add manga_name column to messages", "ALTER TABLE messages ADD COLUMN manga_name TEXT"),
    # v2: library.password_hash — lets an interrupted encrypted upload verify
    # the resumed password against the one used for already-uploaded chunks.
    (2, "add password_hash column to library (vault resume support)",
     "ALTER TABLE library ADD COLUMN password_hash TEXT"),
]
