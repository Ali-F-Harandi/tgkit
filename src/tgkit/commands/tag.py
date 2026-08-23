"""`tgkit tag` — manage tags on library entries.

Tags are stored in the DB (tags table). They're metadata labels that
can be attached to any library entry (copied file, vault file, etc.)

Usage:
    tgkit tag add <library_id> <tag>
    tgkit tag remove <library_id> <tag>
    tgkit tag list <library_id>
    tgkit tag search <tag>
"""

from __future__ import annotations

import argparse
import json
import logging
import time

from rich.console import Console
from rich.table import Table

from tgkit.config.schema import Config
from tgkit.db.connection import get_db
from tgkit.db.store import Store
from tgkit.db.sync import DBSync

logger = logging.getLogger(__name__)
console = Console()


async def cmd_tag(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Manage tags on library entries."""
    db = get_db(config.get_db_path())
    store = Store(db)

    action = args.tag_cmd

    if action == "add":
        return await _tag_add(args, config, store, db)
    elif action == "remove":
        return await _tag_remove(args, config, store, db)
    elif action == "list":
        return await _tag_list(args, store)
    elif action == "search":
        return await _tag_search(args, store)
    else:
        console.print(f"[red]✗[/red] Unknown tag action: {action}")
        return 1


async def _tag_add(args, config, store, db) -> int:
    """Add a tag to a library entry."""
    library_id = args.library_id
    tag = args.tag.strip().lower()

    # Verify library entry exists
    entry = store.library.get(library_id)
    if not entry:
        console.print(f"[red]✗[/red] Library entry #{library_id} not found.")
        return 1

    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tags (file_id, tag, created_at) VALUES (?, ?, ?)",
                (library_id, tag, int(time.time())),
            )
        console.print(f"[green]✓[/green] Added tag '{tag}' to library #{library_id}")
    except Exception as e:
        console.print(f"[red]✗[/red] Failed: {e}")
        return 1

    # Auto-sync DB
    if config.channels.db_sync and config.db.auto_sync:
        db_sync = DBSync(config, config.get_db_path())
        try:
            db_sync.upload(description=f"Tag add: #{library_id} '{tag}'")
        finally:
            db_sync.close()

    return 0


async def _tag_remove(args, config, store, db) -> int:
    """Remove a tag from a library entry."""
    library_id = args.library_id
    tag = args.tag.strip().lower()

    with db.connect() as conn:
        cur = conn.execute(
            "DELETE FROM tags WHERE file_id = ? AND tag = ?",
            (library_id, tag),
        )
    if cur.rowcount > 0:
        console.print(f"[green]✓[/green] Removed tag '{tag}' from library #{library_id}")
    else:
        console.print(f"[yellow]⚠[/yellow] Tag '{tag}' was not on library #{library_id}")

    # Auto-sync DB
    if config.channels.db_sync and config.db.auto_sync:
        db_sync = DBSync(config, config.get_db_path())
        try:
            db_sync.upload(description=f"Tag remove: #{library_id} '{tag}'")
        finally:
            db_sync.close()

    return 0


async def _tag_list(args, store) -> int:
    """List tags on a library entry."""
    library_id = args.library_id

    entry = store.library.get(library_id)
    if not entry:
        console.print(f"[red]✗[/red] Library entry #{library_id} not found.")
        return 1

    tags = db_query_all(
        "SELECT tag, created_at FROM tags WHERE file_id = ? ORDER BY tag",
        (library_id,)
    )

    console.print(f"[bold]Library #{library_id}:[/bold] {entry['name']}")
    if tags:
        console.print(f"  Tags ({len(tags)}):")
        for t in tags:
            console.print(f"    • {t['tag']}")
    else:
        console.print(f"  [dim]No tags[/dim]")

    return 0


async def _tag_search(args, store) -> int:
    """Search library entries by tag."""
    tag = args.tag.strip().lower()

    rows = db_query_all(
        """SELECT l.id, l.name, l.size, l.share_link, t.tag
           FROM library l
           JOIN tags t ON t.file_id = l.id
           WHERE t.tag = ?
           ORDER BY l.uploaded_at DESC""",
        (tag,)
    )

    if not rows:
        console.print(f"[yellow]⚠[/yellow] No library entries with tag '{tag}'")
        return 0

    table = Table(title=f"Library entries tagged '{tag}' ({len(rows)})", show_header=True)
    table.add_column("ID", style="cyan", width=5)
    table.add_column("Name", style="green", width=40)
    table.add_column("Size", justify="right", width=10)
    table.add_column("Link", style="dim", width=40)

    for r in rows:
        size_mb = r["size"] / (1024 * 1024) if r["size"] else 0
        table.add_row(
            str(r["id"]),
            r["name"][:38],
            f"{size_mb:.1f} MB",
            r["share_link"] or "",
        )

    console.print(table)
    return 0


# Helper to avoid circular import
def db_query_all(sql, params):
    from tgkit.db.connection import get_db
    db = get_db()
    return db.query_all(sql, params)
