"""Database connection — SQLite with WAL mode + context manager.

One DB file at ~/.tgkit/tgkit.db (or path from config).
Connections are opened per-operation via context manager (thread-safe).
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from tgkit.db.schema import SCHEMA_SQL, MIGRATIONS

logger = logging.getLogger(__name__)


class Database:
    """SQLite database wrapper.

    Usage:
        db = Database(Path("~/.tgkit/tgkit.db").expanduser())
        db.init()  # create tables if not exist
        with db.connect() as conn:
            conn.execute("SELECT ...")
    """

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def init(self) -> None:
        """Create all tables + apply migrations. Safe to call multiple times."""
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            self._apply_migrations(conn)
        logger.debug(f"Database initialized at {self.path}")

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        """Apply any pending migrations (additive ALTER TABLE)."""
        # Ensure schema_version table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version    INTEGER PRIMARY KEY,
                applied_at INTEGER NOT NULL,
                description TEXT
            )
        """)
        conn.commit()

        # Get current version
        cur = conn.execute("SELECT MAX(version) FROM schema_version")
        row = cur.fetchone()
        current_version = row[0] or 0

        # Apply pending migrations
        import time
        for version, description, sql in MIGRATIONS:
            if version > current_version:
                logger.info(f"Applying migration v{version}: {description}")
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at, description) VALUES (?, ?, ?)",
                    (version, int(time.time()), description),
                )
                conn.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Context manager: yields a connection with WAL mode + foreign keys on.

        The connection is closed when the context exits.
        """
        conn = sqlite3.connect(
            str(self.path),
            timeout=30,           # wait up to 30s on lock
            check_same_thread=False,
        )
        try:
            # Enable WAL mode for better concurrency
            conn.execute("PRAGMA journal_mode=WAL")
            # Enable foreign keys
            conn.execute("PRAGMA foreign_keys=ON")
            # Set busy timeout
            conn.execute("PRAGMA busy_timeout=30000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a single statement (convenience method)."""
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return cur  # note: results are fetched before connection closes

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        """Execute a statement with multiple parameter sets."""
        with self.connect() as conn:
            conn.executemany(sql, params_list)

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        """Execute a query and return the first row as a dict, or None."""
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def query_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Execute a query and return all rows as list of dicts."""
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def __repr__(self) -> str:
        return f"Database({self.path})"


# ============================================================================
# Module-level helpers
# ============================================================================

_default_db: Database | None = None


def get_db(path: Path | str | None = None) -> Database:
    """Get or create the default Database instance.

    If path is None, uses ~/.tgkit/tgkit.db.
    The instance is cached for the process lifetime.
    """
    global _default_db
    if _default_db is None or (path and _default_db.path != Path(path).expanduser()):
        if path:
            _default_db = Database(path)
        else:
            _default_db = Database(Path("~/.tgkit/tgkit.db").expanduser())
        _default_db.init()
    return _default_db


def init_db(path: Path | str | None = None) -> Database:
    """Initialize the database (create tables). Returns the Database instance."""
    db = get_db(path)
    db.init()
    return db
