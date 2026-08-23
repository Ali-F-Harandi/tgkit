"""`tgkit stats` — overall statistics (bots, channels, DB, throughput)."""

from __future__ import annotations

import argparse
import logging

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from tgkit.config.schema import Config
from tgkit.capabilities import detect_capabilities
from tgkit.db.connection import get_db

logger = logging.getLogger(__name__)
console = Console()


async def cmd_stats(args: argparse.Namespace, config: Config) -> int:
    """Show overall tgkit statistics."""
    report = detect_capabilities(config)

    # ── Tier ──
    if report.tier == "tier2_extended":
        tier_color = "green"
        tier_label = "Tier 2 — Extended (MTProto)"
    elif report.tier == "tier1_basic":
        tier_color = "yellow"
        tier_label = "Tier 1 — Basic (Bot API only)"
    else:
        tier_color = "red"
        tier_label = "Not configured"

    console.print(Panel(
        f"[{tier_color}]{tier_label}[/{tier_color}]",
        title="tgkit stats",
        border_style=tier_color,
    ))

    # ── Bots ──
    bot_table = Table(title=f"Bots ({len(config.bots)})", show_header=True)
    bot_table.add_column("#", style="dim", width=3)
    bot_table.add_column("Bot ID", style="cyan")
    bot_table.add_column("Username", style="green")
    for idx, bot in enumerate(config.bots):
        bot_table.add_row(str(idx), str(bot.bot_id), f"@{bot.username}" if bot.username else "?")
    console.print(bot_table)

    # ── DB stats ──
    db = get_db(config.get_db_path())

    db_table = Table(title="Database", show_header=True)
    db_table.add_column("Table", style="cyan")
    db_table.add_column("Rows", justify="right", style="green")

    total_rows = 0
    for table_name in ["channels", "bots", "scans", "messages", "library",
                       "chunks", "tags", "downloads", "operations_log", "orphans"]:
        try:
            result = db.query_one(f"SELECT COUNT(*) as count FROM {table_name}")
            count = result["count"] if result else 0
            db_table.add_row(table_name, str(count))
            total_rows += count
        except Exception:
            db_table.add_row(table_name, "[dim]?[/dim]")

    db_table.add_row("[bold]TOTAL[/bold]", f"[bold]{total_rows}[/bold]")
    console.print(db_table)

    # ── Operations breakdown ──
    ops = db.query_all("""
        SELECT op_type, status, COUNT(*) as count
        FROM operations_log
        GROUP BY op_type, status
        ORDER BY op_type, status
    """)

    if ops:
        ops_table = Table(title="Operations Breakdown", show_header=True)
        ops_table.add_column("Operation", style="cyan")
        ops_table.add_column("Status", width=10)
        ops_table.add_column("Count", justify="right")

        for r in ops:
            color = "green" if r["status"] == "ok" else "red"
            ops_table.add_row(r["op_type"], f"[{color}]{r['status']}[/{color}]", str(r["count"]))

        console.print(ops_table)

    # ── Channels ──
    channels = db.query_all("SELECT role, COUNT(*) as count FROM channels GROUP BY role ORDER BY role")
    if channels:
        ch_table = Table(title="Channels", show_header=True)
        ch_table.add_column("Role", style="cyan")
        ch_table.add_column("Count", justify="right")
        for r in channels:
            ch_table.add_row(r["role"], str(r["count"]))
        console.print(ch_table)

    # ── Config summary ──
    console.print(f"\n[bold]Config:[/bold]")
    console.print(f"  DB path:       {config.get_db_path()}")
    console.print(f"  Session dir:   {config.get_session_dir()}")
    console.print(f"  Destination:   {config.channels.default_destination or '[dim]not set[/dim]'}")
    console.print(f"  DB sync chan:  {config.channels.db_sync or '[dim]not set[/dim]'}")
    console.print(f"  Auto-sync:     {'✓ on' if config.db.auto_sync else '✗ off'}")
    console.print(f"  Throughput:    ~{config.expected_throughput():.1f} req/s")

    return 0
