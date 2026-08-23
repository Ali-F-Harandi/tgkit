"""`tgkit db` commands — sync, download, status, find."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from rich.console import Console
from rich.table import Table

from tgkit.config.schema import Config
from tgkit.config.loader import save_config
from tgkit.db.sync import DBSync
from tgkit.db.connection import get_db
from tgkit.db.store import Store
from tgkit.capabilities import detect_capabilities

logger = logging.getLogger(__name__)
console = Console()


async def cmd_db_sync(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Upload local DB to the sync channel."""
    report = detect_capabilities(config)
    if not report.has_db_sync_channel:
        console.print("[red]✗[/red] No DB sync channel configured.")
        console.print("    Run: [bold]tgkit channel add <ID> --role db_sync[/bold]")
        return 1

    if not config.bots:
        console.print("[red]✗[/red] No bots configured.")
        return 1

    db_path = config.get_db_path()
    db_sync = DBSync(config, db_path)

    try:
        console.print(f"[bold]Syncing DB to channel {config.channels.db_sync}...[/bold]")
        msg_id = db_sync.upload(description=args.description or "")

        if msg_id:
            console.print(f"[green]✓[/green] DB synced (msg_id={msg_id})")
            console.print(f"    Local: {db_path} ({db_path.stat().st_size:,} bytes)")
            return 0
        else:
            console.print("[red]✗[/red] DB sync failed.")
            return 1
    finally:
        db_sync.close()


async def cmd_db_download(args: argparse.Namespace, config: Config) -> int:
    """Download DB from the sync channel."""
    report = detect_capabilities(config)
    if not report.has_db_sync_channel:
        console.print("[red]✗[/red] No DB sync channel configured.")
        return 1

    if not config.bots:
        console.print("[red]✗[/red] No bots configured.")
        return 1

    db_path = config.get_db_path()
    db_sync = DBSync(config, db_path)

    try:
        console.print(f"[bold]Downloading DB from channel {config.channels.db_sync}...[/bold]")
        if db_sync.download():
            console.print(f"[green]✓[/green] DB downloaded to: {db_path}")
            console.print(f"    Size: {db_path.stat().st_size:,} bytes")
            return 0
        else:
            console.print("[yellow]⚠[/yellow] DB not found in channel (first run?)")
            return 1
    finally:
        db_sync.close()


async def cmd_db_find(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Discover DB in the sync channel by scanning recent messages."""
    report = detect_capabilities(config)
    if not report.has_db_sync_channel:
        console.print("[red]✗[/red] No DB sync channel configured.")
        return 1

    if not config.bots:
        console.print("[red]✗[/red] No bots configured.")
        return 1

    db_path = config.get_db_path()
    db_sync = DBSync(config, db_path)

    try:
        console.print(f"[bold]Searching for DB in channel {config.channels.db_sync}...[/bold]")
        console.print("[dim]Scanning last 200 messages...[/dim]")
        msg_id = db_sync.discover()

        if msg_id:
            console.print(f"[green]✓[/green] DB found at msg_id={msg_id}")
            console.print(f"    Saved to config: {config_path}")
            return 0
        else:
            console.print("[yellow]⚠[/yellow] DB not found in last 200 messages.")
            console.print("    If this is a first run, use [bold]tgkit db sync[/bold] to upload.")
            return 1
    finally:
        db_sync.close()


async def cmd_db_status(args: argparse.Namespace, config: Config) -> int:
    """Show DB sync status + local DB stats."""
    report = detect_capabilities(config)
    db_path = config.get_db_path()

    db_sync = DBSync(config, db_path)
    status = db_sync.status()

    table = Table(title="DB Status", show_header=True)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Sync channel", str(status["sync_channel"] or "not set"))
    table.add_row("Sync msg_id", str(status["sync_msg_id"] or "not set"))
    table.add_row("Local path", status["local_path"])
    table.add_row("Local exists", "✓" if status["local_exists"] else "✗")
    table.add_row("Local size", f"{status['local_size']:,} bytes")
    table.add_row("Auto-sync", "✓ on" if status["auto_sync"] else "✗ off")

    console.print(table)

    # Show local DB stats if it exists
    if status["local_exists"]:
        db = get_db(db_path)
        store = Store(db)

        # Table counts
        counts_table = Table(title="Local DB Contents", show_header=True)
        counts_table.add_column("Table", style="cyan")
        counts_table.add_column("Rows", style="green", justify="right")

        for table_name in ["channels", "bots", "scans", "messages", "library",
                          "chunks", "tags", "downloads", "operations_log", "orphans"]:
            try:
                result = db.query_one(f"SELECT COUNT(*) as count FROM {table_name}")
                counts_table.add_row(table_name, str(result["count"] if result else 0))
            except Exception:
                counts_table.add_row(table_name, "[dim]?[/dim]")

        console.print(counts_table)

        # Recent operations
        recent_ops = store.ops.list_recent(limit=5)
        if recent_ops:
            ops_table = Table(title="Recent Operations (last 5)", show_header=True)
            ops_table.add_column("Time", style="dim")
            ops_table.add_column("Type", style="cyan")
            ops_table.add_column("Status", style="yellow")
            ops_table.add_column("Dest", style="green")

            from datetime import datetime
            for op in recent_ops:
                ts = datetime.fromtimestamp(op["started_at"]).strftime("%m-%d %H:%M")
                dest = f"{op['dest_channel']}/{op['dest_msg_id']}" if op["dest_msg_id"] else "—"
                ops_table.add_row(ts, op["op_type"], op["status"], dest)

            console.print(ops_table)

    db_sync.close()
    return 0
