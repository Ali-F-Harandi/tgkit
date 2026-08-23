"""`tgkit status` — show capabilities, config, DB sync state."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from tgkit.config.schema import Config
from tgkit.capabilities import detect_capabilities, CAPABILITY_DESCRIPTIONS, Capability
from tgkit.db.sync import DBSync

logger = logging.getLogger(__name__)
console = Console()


async def cmd_status(args: argparse.Namespace, config: Config) -> int:
    """Show current capabilities, config summary, and DB sync status."""
    report = detect_capabilities(config)

    # ── Tier banner ──
    if report.tier == "tier2_extended":
        tier_color = "green"
        tier_label = "Tier 2 — Extended (MTProto)"
        tier_desc = "Full capabilities: scan, copy without header, large files, DB discovery"
    elif report.tier == "tier1_basic":
        tier_color = "yellow"
        tier_label = "Tier 1 — Basic (Bot API only)"
        tier_desc = "Limited: forward (with header), send, edit, delete. No scanning."
    else:
        tier_color = "red"
        tier_label = "Not configured"
        tier_desc = "Run `tgkit init` and `tgkit bot add <TOKEN>` to get started"

    console.print(Panel(
        f"[{tier_color}]{tier_label}[/{tier_color}]\n{tier_desc}",
        title="tgkit status",
        border_style=tier_color,
    ))

    # ── Config summary ──
    config_table = Table(title="Configuration", show_header=True)
    config_table.add_column("Field", style="cyan")
    config_table.add_column("Value", style="green")

    config_table.add_row("Bots configured", str(report.bot_count))
    config_table.add_row("API credentials", "✓ set" if report.has_api_credentials else "✗ not set")
    config_table.add_row("Destination channel",
                         str(config.channels.default_destination) if report.has_destination_channel else "✗ not set")
    config_table.add_row("DB sync channel",
                         str(config.channels.db_sync) if report.has_db_sync_channel else "✗ not set")
    config_table.add_row("Session dir", config.api.session_dir)
    config_table.add_row("DB path", str(config.get_db_path()))

    console.print(config_table)

    # ── Missing for Tier 2 ──
    if report.missing_for_tier2:
        console.print()
        console.print("[yellow]To unlock Tier 2 (MTProto) capabilities:[/yellow]")
        for item in report.missing_for_tier2:
            console.print(f"  • {item}")

    # ── Capabilities ──
    cap_table = Table(title="Available Capabilities", show_header=True)
    cap_table.add_column("Capability", style="cyan")
    cap_table.add_column("Status", justify="center")
    cap_table.add_column("Description", style="dim")

    for cap, desc in CAPABILITY_DESCRIPTIONS.items():
        available = report.can(cap)
        status_str = "[green]✓[/green]" if available else "[red]✗[/red]"
        cap_table.add_row(cap.name, status_str, desc)

    console.print(cap_table)

    # ── DB sync status ──
    if report.has_db_sync_channel:
        console.print()
        db_sync = DBSync(config, config.get_db_path())
        status = db_sync.status()

        db_table = Table(title="DB Sync Status", show_header=True)
        db_table.add_column("Field", style="cyan")
        db_table.add_column("Value", style="green")

        db_table.add_row("Sync channel", str(status["sync_channel"]))
        db_table.add_row("Sync msg_id", str(status["sync_msg_id"] or "not set"))
        db_table.add_row("Local path", status["local_path"])
        db_table.add_row("Local exists", "✓" if status["local_exists"] else "✗")
        db_table.add_row("Local size", f"{status['local_size']:,} bytes")
        db_table.add_row("Auto-sync", "✓ on" if status["auto_sync"] else "✗ off")

        console.print(db_table)

    # ── Next steps ──
    console.print()
    console.print("[bold]Next steps:[/bold]")
    if report.tier == "none":
        console.print("  1. [bold]tgkit init[/bold] — create config")
        console.print("  2. [bold]tgkit bot add <TOKEN>[/bold] — add a bot")
    elif report.tier == "tier1_basic":
        console.print("  • Set api_id + api_hash in config to unlock Tier 2")
        console.print("  • [bold]tgkit channel add <ID> --role db_sync[/bold] — set up DB sync")
    else:
        if not report.has_destination_channel:
            console.print("  • [bold]tgkit channel add <ID> --role destination[/bold]")
        if not report.has_db_sync_channel:
            console.print("  • [bold]tgkit channel add <ID> --role db_sync[/bold]")
        if report.has_db_sync_channel:
            console.print("  • [bold]tgkit db download[/bold] — download existing DB")
            console.print("  • [bold]tgkit scan <link>[/bold] — scan a channel")

    return 0
