"""`tgkit library` — query and manage the library (copied/vault files)."""

from __future__ import annotations

import argparse
import logging

from rich.console import Console
from rich.table import Table

from tgkit.config.schema import Config
from tgkit.db.connection import get_db
from tgkit.db.store import Store

logger = logging.getLogger(__name__)
console = Console()


async def cmd_library(args: argparse.Namespace, config: Config) -> int:
    """Library management."""
    action = args.library_cmd

    if action == "list":
        return await _lib_list(args, config)
    elif action == "search":
        return await _lib_search(args, config)
    elif action == "info":
        return await _lib_info(args, config)
    elif action == "stats":
        return await _lib_stats(args, config)
    else:
        console.print(f"[red]✗[/red] Unknown library action: {action}")
        return 1


async def _lib_list(args, config) -> int:
    """List library entries."""
    db = get_db(config.get_db_path())
    store = Store(db)

    limit = args.limit or 20
    kind = args.kind

    entries = store.library.list_recent(limit=limit, kind=kind)

    if not entries:
        console.print("[yellow]No library entries.[/yellow]")
        return 0

    table = Table(title=f"Library ({len(entries)} entries)", show_header=True)
    table.add_column("ID", style="cyan", width=5)
    table.add_column("Name", style="green", width=40)
    table.add_column("Kind", style="yellow", width=8)
    table.add_column("Size", justify="right", width=10)
    table.add_column("Status", width=10)
    table.add_column("Uploaded", style="dim", width=18)

    from datetime import datetime
    for e in entries:
        size_mb = e["size"] / (1024 * 1024) if e["size"] else 0
        uploaded = datetime.fromtimestamp(e["uploaded_at"]).strftime("%m-%d %H:%M") if e["uploaded_at"] else ""
        table.add_row(
            str(e["id"]),
            e["name"][:38],
            e["kind"],
            f"{size_mb:.1f} MB",
            e["status"],
            uploaded,
        )

    console.print(table)
    return 0


async def _lib_search(args, config) -> int:
    """Search library by name."""
    db = get_db(config.get_db_path())
    store = Store(db)

    query = args.query
    entries = store.library.search(query, limit=args.limit or 50)

    if not entries:
        console.print(f"[yellow]No matches for '{query}'[/yellow]")
        return 0

    table = Table(title=f"Search: '{query}' ({len(entries)} matches)", show_header=True)
    table.add_column("ID", style="cyan", width=5)
    table.add_column("Name", style="green", width=50)
    table.add_column("Size", justify="right", width=10)
    table.add_column("Link", style="dim", width=40)

    for e in entries:
        size_mb = e["size"] / (1024 * 1024) if e["size"] else 0
        table.add_row(
            str(e["id"]),
            e["name"][:48],
            f"{size_mb:.1f} MB",
            e["share_link"] or "",
        )

    console.print(table)
    return 0


async def _lib_info(args, config) -> int:
    """Show detailed info about a library entry."""
    db = get_db(config.get_db_path())
    store = Store(db)

    entry = store.library.get(args.library_id)
    if not entry:
        console.print(f"[red]✗[/red] Library entry #{args.library_id} not found.")
        return 1

    console.print(f"[bold]Library #{entry['id']}[/bold]")
    console.print(f"  Name:       {entry['name']}")
    console.print(f"  Kind:       {entry['kind']}")
    console.print(f"  Status:     {entry['status']}")
    size_mb = entry["size"] / (1024 * 1024) if entry["size"] else 0
    console.print(f"  Size:       {entry['size']:,} bytes ({size_mb:.1f} MB)")
    console.print(f"  SHA256:     {entry['sha256'][:32]}...")
    console.print(f"  Share link: {entry['share_link'] or '[dim]none[/dim]'}")
    console.print(f"  Channel:    {entry['main_channel']}")
    console.print(f"  Parts:      {entry['total_parts']}")
    console.print(f"  Encrypted:  {'yes' if entry['encrypted'] else 'no'}")
    console.print(f"  Compressed: {'yes' if entry['compressed'] else 'no'}")

    # Show tags
    tags = db.query_all("SELECT tag FROM tags WHERE file_id = ? ORDER BY tag", (entry["id"],))
    if tags:
        console.print(f"  Tags:       {', '.join(t['tag'] for t in tags)}")
    else:
        console.print(f"  Tags:       [dim]none[/dim]")

    return 0


async def _lib_stats(args, config) -> int:
    """Show library statistics."""
    db = get_db(config.get_db_path())

    # Total entries
    result = db.query_one("SELECT COUNT(*) as count, COALESCE(SUM(size), 0) as total_size FROM library WHERE status = 'uploaded'")
    total_count = result["count"] if result else 0
    total_size = result["total_size"] if result else 0

    # By kind
    by_kind = db.query_all(
        "SELECT kind, COUNT(*) as count, SUM(size) as size FROM library WHERE status = 'uploaded' GROUP BY kind"
    )

    # By status
    by_status = db.query_all(
        "SELECT status, COUNT(*) as count FROM library GROUP BY status"
    )

    console.print(f"[bold]Library Statistics[/bold]")
    console.print(f"  Total entries: [cyan]{total_count}[/cyan]")
    console.print(f"  Total size:    [cyan]{total_size / (1024**3):.2f} GB[/cyan]")

    if by_kind:
        console.print(f"\n[bold]By kind:[/bold]")
        for r in by_kind:
            size_gb = (r["size"] or 0) / (1024**3)
            console.print(f"  {r['kind']:10s}  {r['count']:5d}  {size_gb:.2f} GB")

    if by_status:
        console.print(f"\n[bold]By status:[/bold]")
        for r in by_status:
            console.print(f"  {r['status']:12s}  {r['count']:5d}")

    return 0
