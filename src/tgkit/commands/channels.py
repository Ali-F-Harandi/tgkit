"""`tgkit channel` commands — add, list, remove, set-role."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from tgkit.config.schema import Config
from tgkit.config.loader import save_config
from tgkit.models.link import parse_channel_ref, build_link
from tgkit.models.channel import Channel
from tgkit.transport.bot_pool import AsyncBotPool
from tgkit.db.connection import get_db
from tgkit.utils import stable_channel_db_id

logger = logging.getLogger(__name__)
console = Console()


# ============================================================================
# channel add
# ============================================================================

async def cmd_channel_add(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Register a channel + resolve its peer (cache access_hash)."""
    ref = args.channel_ref
    role = args.role
    channel_id = parse_channel_ref(ref)

    console.print(f"[bold]Adding channel:[/bold] {ref}")
    console.print(f"  Role: {role}")
    console.print(f"  Normalized ID: {channel_id}")

    if not config.bots:
        console.print(f"\n[red]✗[/red] No bots configured. Add one first: [bold]tgkit bot add <TOKEN>[/bold]")
        return 1

    if not config.api.api_id or not config.api.api_hash:
        console.print(f"\n[red]✗[/red] api_id / api_hash not set in config.")
        return 1

    # Resolve peer via pyrofork
    console.print(f"\n[dim]Resolving peer (caching access_hash in session)...[/dim]")
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

        # For numeric IDs, resolve peer on all bots
        if isinstance(channel_id, int):
            success = await pool.resolve_peer_all(channel_id)
            if success == 0:
                console.print(f"[red]✗[/red] Failed to resolve peer for channel {channel_id}")
                console.print("    Make sure the bot is a member of the channel.")
                return 1
            console.print(f"[green]✓[/green] Peer resolved on {success}/{len(pool.bots)} bots")

            # Try to get channel info
            bot = await pool.get_next()
            try:
                chat = await bot.client.raw.get_chat(channel_id)
                title = chat.title
                chat_type = "public" if chat.username else "private"
                username = chat.username
                console.print(f"[green]✓[/green] Channel info: [bold]{title}[/bold] ({chat_type})")
            except Exception as e:
                logger.debug(f"get_chat failed: {e}")
                title = None
                chat_type = None
                username = None
        else:
            # Username form — get_chat to verify
            bot = await pool.get_next()
            try:
                chat = await bot.client.raw.get_chat(channel_id)
                title = chat.title
                chat_type = "public" if chat.username else "private"
                username = (chat.username or channel_id.lstrip("@"))
                console.print(f"[green]✓[/green] Channel info: [bold]{title}[/bold] ({chat_type})")
            except Exception as e:
                console.print(f"[red]✗[/red] Failed to get channel info: {e}")
                return 1

        # Store in DB
        db = get_db(config.get_db_path())
        with db.connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO channels
                    (id, username, access_hash, title, type, role, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                stable_channel_db_id(channel_id),
                username,
                None,  # access_hash not directly available
                title,
                chat_type,
                role,
                int(time.time()),
            ))

        # Update config based on role
        if role == "destination":
            config.channels.default_destination = channel_id if isinstance(channel_id, int) else None
        elif role == "vault_main":
            config.channels.vault_main = channel_id if isinstance(channel_id, int) else None
        elif role == "vault_temp":
            config.channels.vault_temp = channel_id if isinstance(channel_id, int) else None
        elif role == "vault_storage":
            if isinstance(channel_id, int) and channel_id not in config.channels.vault_storage:
                config.channels.vault_storage.append(channel_id)
        elif role == "db_sync":
            config.channels.db_sync = channel_id if isinstance(channel_id, int) else None

        save_config(config, config_path)
        console.print(f"\n[green]✓[/green] Channel added with role [bold]{role}[/bold]")
        console.print(f"  Config updated: {config_path}")

        return 0

    except Exception as e:
        console.print(f"\n[red]✗[/red] Error: {type(e).__name__}: {e}")
        logger.exception("Channel add failed")
        return 1

    finally:
        try:
            await pool.stop_all()
        except Exception:
            pass


# ============================================================================
# channel list
# ============================================================================

async def cmd_channel_list(args: argparse.Namespace, config: Config) -> int:
    """List all registered channels."""
    db = get_db(config.get_db_path())

    channels = db.query_all("SELECT * FROM channels ORDER BY role, added_at")

    if not channels:
        console.print("[yellow]No channels registered.[/yellow]")
        console.print("Add one with: [bold]tgkit channel add <@username|-100ID> --role <ROLE>[/bold]")
        return 0

    table = Table(title=f"Registered Channels ({len(channels)})", show_header=True)
    table.add_column("ID/Username", style="cyan", width=25)
    table.add_column("Title", style="green", width=30)
    table.add_column("Type", style="yellow", width=10)
    table.add_column("Role", style="magenta", width=15)
    table.add_column("Last Scanned", style="dim", width=20)

    for ch in channels:
        ch_id = ch["id"]
        username = ch["username"]
        display = f"@{username}" if username else str(ch_id)
        title = ch["title"] or "[dim]?[/dim]"
        ch_type = ch["type"] or "[dim]?[/dim]"
        role = ch["role"]
        last_scanned = ch["last_scanned_at"]
        if last_scanned:
            from datetime import datetime
            ls_str = datetime.fromtimestamp(last_scanned).strftime("%Y-%m-%d %H:%M")
        else:
            ls_str = "[dim]never[/dim]"
        table.add_row(display, title, ch_type, role, ls_str)

    console.print(table)

    # Show config role assignments
    console.print("\n[bold]Config role assignments:[/bold]")
    cc = config.channels
    if cc.default_destination:
        console.print(f"  default_destination: {cc.default_destination}")
    if cc.vault_main:
        console.print(f"  vault_main:          {cc.vault_main}")
    if cc.vault_temp:
        console.print(f"  vault_temp:          {cc.vault_temp}")
    if cc.vault_storage:
        console.print(f"  vault_storage:       {cc.vault_storage}")
    if cc.db_sync:
        console.print(f"  db_sync:             {cc.db_sync}")

    return 0


# ============================================================================
# channel remove
# ============================================================================

async def cmd_channel_remove(args: argparse.Namespace, config: Config) -> int:
    """Remove a channel by ID or username."""
    ref = args.channel_ref
    channel_id = parse_channel_ref(ref)

    db = get_db(config.get_db_path())

    # Find the channel
    if isinstance(channel_id, int):
        rows = db.query_all("SELECT * FROM channels WHERE id = ?", (channel_id,))
    else:
        # Username — search by username
        username = channel_id.lstrip("@")
        rows = db.query_all("SELECT * FROM channels WHERE username = ?", (username,))

    if not rows:
        console.print(f"[red]✗[/red] Channel not found: {ref}")
        return 1

    ch = rows[0]
    display = f"@{ch['username']}" if ch["username"] else str(ch["id"])
    console.print(f"Removing channel: [bold]{display}[/bold] (role: {ch['role']})")

    # Remove from DB
    db.execute("DELETE FROM channels WHERE id = ?", (ch["id"],))
    console.print(f"[green]✓[/green] Removed from database")

    # Clear from config if it was a role assignment
    cc = config.channels
    changed = False
    if cc.default_destination == ch["id"]:
        cc.default_destination = None
        changed = True
    if cc.vault_main == ch["id"]:
        cc.vault_main = None
        changed = True
    if cc.vault_temp == ch["id"]:
        cc.vault_temp = None
        changed = True
    if ch["id"] in cc.vault_storage:
        cc.vault_storage.remove(ch["id"])
        changed = True
    if cc.db_sync == ch["id"]:
        cc.db_sync = None
        changed = True

    if changed:
        from tgkit.config.loader import save_config
        save_config(config)
        console.print(f"[green]✓[/green] Cleared from config role assignments")

    return 0
