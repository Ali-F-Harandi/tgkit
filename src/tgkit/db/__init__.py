"""Database subsystem: SQLite connection + schema."""

from __future__ import annotations

from tgkit.db.connection import Database, get_db, init_db
from tgkit.db.schema import SCHEMA_SQL, MIGRATIONS

__all__ = ["Database", "get_db", "init_db", "SCHEMA_SQL", "MIGRATIONS"]
