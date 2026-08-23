"""`tgkit bot` commands — add, list, remove, test."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import requests
from rich.console import Console
from rich.table import Table

from tgkit.config.schema import Config
from tgkit.config.loader import save_config
from tgkit.transport.bot_pool import AsyncBotPool

logger = logging.getLogger(__name__)
console = Console()


# ============================================================================
# bot add
# ============================================================================

async def cmd_bot_add(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Add a bot token to config (validates via getMe first)."""
    token = args.token.strip()

    if not token or ":" not in token:
        console.print(f"[red]✗[/red] Invalid token format: {token!r}")
        console.print("    Expected: <bot_id>:<token_hash> (e.g., 8866132706:AAH_xxx...)")
        return 1

    # Check for duplicates
    if config.find_bot_by_token(token):
        console.print(f"[yellow]⚠[/yellow] This bot is already in config.")
        return 1

    # Validate via Bot API getMe (the ONLY Bot API HTTP call in tgkit)
    console.print(f"[dim]Validating bot token via getMe...[/dim]")
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=10,
        )
        data = r.json()
    except requests.RequestException as e:
        console.print(f"[red]✗[/red] Network error: {e}")
        return 1

    if not data.get("ok"):
        console.print(f"[red]✗[/red] Invalid bot token: {data.get('description', 'unknown error')}")
        return 1

    bot_info = data["result"]
    console.print(f"[green]✓[/green] Valid bot:")
    console.print(f"    id:    [bold]{bot_info['id']}[/bold]")
    console.print(f"    name:  @{bot_info['username']}")
    console.print(f"    title: {bot_info.get('first_name', '?')}")

    # Add to config
    config.add_bot(token, username=bot_info["username"])
    save_config(config, config_path)

    console.print(f"[green]✓[/green] Added bot to config ({len(config.bots)} total)")
    console.print(f"    Saved to: [bold]{config_path}[/bold]")

    # Show expected throughput
    if len(config.bots) > 0:
        rps = config.expected_throughput()
        console.print(f"\n[dim]Expected throughput: ~{rps:.1f} req/s "
                      f"({len(config.bots)} bots × {1/config.throttle.min_interval:.1f} req/s/bot)[/dim]")

    return 0


# ============================================================================
# bot list
# ============================================================================

async def cmd_bot_list(args: argparse.Namespace, config: Config) -> int:
    """List all configured bots."""
    if not config.bots:
        console.print("[yellow]No bots configured.[/yellow]")
        console.print("Add one with: [bold]tgkit bot add <TOKEN>[/bold]")
        return 0

    table = Table(title=f"Configured Bots ({len(config.bots)})", show_header=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Bot ID", style="cyan")
    table.add_column("Username", style="green")
    table.add_column("Status", style="yellow")

    for idx, bot in enumerate(config.bots):
        table.add_row(
            str(idx),
            str(bot.bot_id),
            f"@{bot.username}" if bot.username else "[dim]?[/dim]",
            "active",
        )

    console.print(table)

    # Throughput estimate
    rps = config.expected_throughput()
    console.print(f"\n[dim]Expected throughput: ~{rps:.1f} req/s "
                  f"({len(config.bots)} bots × {1/config.throttle.min_interval:.1f} req/s/bot)[/dim]")
    console.print(f"[dim]With smart throttle, this adapts to FloodWait automatically.[/dim]")

    return 0


# ============================================================================
# bot remove
# ============================================================================

async def cmd_bot_remove(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Remove a bot by index."""
    idx = args.index

    if idx < 0 or idx >= len(config.bots):
        console.print(f"[red]✗[/red] Invalid index {idx}. Valid: 0..{len(config.bots) - 1}")
        return 1

    removed = config.remove_bot(idx)
    save_config(config, config_path)

    console.print(f"[green]✓[/green] Removed bot #{idx}: @{removed.username}")
    console.print(f"    Remaining: {len(config.bots)} bot(s)")

    return 0


# ============================================================================
# bot test
# ============================================================================

async def cmd_bot_test(args: argparse.Namespace, config: Config) -> int:
    """Test connectivity of all bots — start each, verify getMe, stop."""
    if not config.bots:
        console.print("[red]✗[/red] No bots configured.")
        console.print("Add one with: [bold]tgkit bot add <TOKEN>[/bold]")
        return 1

    if not config.api.api_id or not config.api.api_hash:
        console.print("[red]✗[/red] api_id / api_hash not set in config.")
        console.print("    Edit config and fill in api_id / api_hash from https://my.telegram.org")
        return 1

    console.print(f"[bold]Testing {len(config.bots)} bot(s)...[/bold]\n")

    tokens = config.bot_tokens
    session_dir = config.get_session_dir()

    try:
        pool = AsyncBotPool(
            tokens=tokens,
            api_id=config.api.api_id,
            api_hash=config.api.api_hash,
            session_dir=str(session_dir),
        )
        await pool.start_all()
        console.print(f"\n[green]✓[/green] All {len(pool.bots)} bot(s) started successfully.\n")
        console.print(pool.stats_summary())

        # Test peer resolution on default destination if set
        if config.channels.default_destination:
            dest = config.channels.default_destination
            console.print(f"\n[bold]Resolving destination channel {dest}...[/bold]")
            success = await pool.resolve_peer_all(dest)
            console.print(f"  Peer resolution: {success}/{len(pool.bots)} bots")
            primed = await pool.prime_destination_all(dest)
            console.print(f"  Destination priming: {primed}/{len(pool.bots)} bots")

        return 0

    except Exception as e:
        console.print(f"\n[red]✗[/red] Error: {type(e).__name__}: {e}")
        logger.exception("Bot test failed")
        return 1

    finally:
        try:
            await pool.stop_all()
        except Exception:
            pass
