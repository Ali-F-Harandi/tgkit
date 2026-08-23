"""`tgkit log` — query the operations log."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from rich.console import Console
from rich.table import Table

from tgkit.config.schema import Config
from tgkit.db.connection import get_db
from tgkit.db.store import Store

logger = logging.getLogger(__name__)
console = Console()


async def cmd_log(args: argparse.Namespace, config: Config) -> int:
    """Query operations log."""
    db = get_db(config.get_db_path())
    store = Store(db)

    if args.stats:
        return await _log_stats(args, store)

    # Build query
    op_type = args.op_type
    status = args.status
    limit = args.limit or 20

    if op_type and status:
        rows = db.query_all(
            """SELECT * FROM operations_log
               WHERE op_type = ? AND status = ?
               ORDER BY started_at DESC LIMIT ?""",
            (op_type, status, limit)
        )
    elif op_type:
        rows = db.query_all(
            """SELECT * FROM operations_log
               WHERE op_type = ?
               ORDER BY started_at DESC LIMIT ?""",
            (op_type, limit)
        )
    elif status:
        rows = db.query_all(
            """SELECT * FROM operations_log
               WHERE status = ?
               ORDER BY started_at DESC LIMIT ?""",
            (status, limit)
        )
    else:
        rows = db.query_all(
            "SELECT * FROM operations_log ORDER BY started_at DESC LIMIT ?",
            (limit,)
        )

    if not rows:
        console.print("[yellow]No operations found.[/yellow]")
        return 0

    table = Table(title=f"Operations Log ({len(rows)} entries)", show_header=True)
    table.add_column("ID", style="dim", width=4)
    table.add_column("Time", style="dim", width=12)
    table.add_column("Op", style="cyan", width=8)
    table.add_column("Status", width=7)
    table.add_column("Src", width=15)
    table.add_column("Dest", width=20)
    table.add_column("Bot", width=10)
    table.add_column("Error", style="red", width=30)

    for r in rows:
        ts = datetime.fromtimestamp(r["started_at"]).strftime("%m-%d %H:%M")
        src = str(r["source_channel"] or "")[:13]
        dest = f"{r['dest_channel']}/{r['dest_msg_id']}" if r["dest_msg_id"] else "—"
        bot = str(r["bot_id"] or "—")[:8]
        error = (r["error"] or "")[:28]
        status_color = "green" if r["status"] == "ok" else "red"
        table.add_row(
            str(r["id"]),
            ts,
            r["op_type"],
            f"[{status_color}]{r['status']}[/{status_color}]",
            src,
            dest,
            bot,
            error,
        )

    console.print(table)
    return 0


async def _log_stats(args, store) -> int:
    """Show operations log statistics."""
    stats = store.ops.stats()

    console.print(f"[bold]Operations Log Statistics[/bold]")

    if not stats["breakdown"]:
        console.print("  [dim]No operations logged.[/dim]")
        return 0

    table = Table(title="Operations by Type/Status", show_header=True)
    table.add_column("Operation", style="cyan", width=12)
    table.add_column("Status", width=10)
    table.add_column("Count", justify="right", width=8)

    for r in stats["breakdown"]:
        status_color = "green" if r["status"] == "ok" else "red"
        table.add_row(
            r["op_type"],
            f"[{status_color}]{r['status']}[/{status_color}]",
            str(r["count"]),
        )

    console.print(table)
    return 0
