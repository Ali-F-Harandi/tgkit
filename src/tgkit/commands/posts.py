"""`tgkit post` commands — create linked-list posts with many links."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from rich.console import Console

from tgkit.config.schema import Config
from tgkit.models.link import parse_channel_ref, build_link
from tgkit.transport.bot_pool import AsyncBotPool
from tgkit.posts.builder import PostBuilder, PostConfig, PostResult, LinkItem
from tgkit.db.connection import get_db
from tgkit.db.store import Store
from tgkit.db.sync import DBSync
from tgkit.capabilities import detect_capabilities, Capability
from tgkit.utils import stable_channel_db_id

logger = logging.getLogger(__name__)
console = Console()


async def cmd_post_create(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Create a linked-list post with many links.

    Examples:
        # From a JSON file with links
        tgkit post create --links-file links.json --header "📚 My Collection"

        # From a scan (pull msg_ids that were copied to dest)
        tgkit post create --from-scan 22 --dest-msg-ids

        # With a cover image
        tgkit post create --links-file links.json --cover cover.jpg --header "📚 Manga"
    """
    report = detect_capabilities(config)
    if not report.can(Capability.SEND_MESSAGE):
        console.print("[red]✗[/red] Post creation requires at least 1 bot.")
        return 1

    if not config.bots:
        console.print("[red]✗[/red] No bots configured.")
        return 1

    # Resolve destination
    dest = args.to or config.channels.default_destination
    if not dest:
        console.print("[red]✗[/red] No destination channel. Use --to or set default_destination.")
        return 1
    # dest can be int (from config) or str (from --to)
    if isinstance(dest, int):
        dest_channel = dest
    else:
        dest_channel = parse_channel_ref(dest)

    # Build links
    links: list[LinkItem] = []

    if args.links_file:
        # Load from JSON: [{"label": "CH1", "url": "https://..."}, ...]
        data = json.loads(Path(args.links_file).read_text(encoding="utf-8"))
        for item in data:
            links.append(LinkItem(label=item["label"], url=item["url"]))
        console.print(f"[green]✓[/green] Loaded {len(links)} links from {args.links_file}")

    elif args.from_scan:
        # Pull from scan results — use the dest_msg_id from operations_log
        db = get_db(config.get_db_path())
        store = Store(db)

        # Get messages from the scan
        scan_id = args.from_scan
        rows = store.messages.query_by_scan(scan_id)

        if not rows:
            console.print(f"[yellow]⚠[/yellow] No messages in scan #{scan_id}")
            return 1

        # For each scanned message, find the corresponding copy in operations_log
        # to get the dest msg_id
        for row in rows:
            source_msg_id = row["msg_id"]
            # Look up the dest msg_id from operations_log
            op = db.query_one(
                """SELECT dest_msg_id FROM operations_log
                   WHERE op_type = ? AND source_msg_id = ? AND status = 'ok'
                   ORDER BY started_at DESC LIMIT 1""",
                (args.op_type or "copy", source_msg_id)
            )
            if op and op["dest_msg_id"]:
                dest_msg_id = op["dest_msg_id"]
                url = build_link(dest_channel, dest_msg_id)
                # Use filename or msg_id as label
                label = row["file_name"] or f"msg_{source_msg_id}"
                # Shorten label if too long
                if len(label) > 50:
                    label = label[:47] + "..."
                links.append(LinkItem(label=label, url=url))

        console.print(f"[green]✓[/green] Built {len(links)} links from scan #{scan_id} + operations_log")

    else:
        console.print("[red]✗[/red] Provide --links-file or --from-scan")
        return 1

    if not links:
        console.print("[yellow]⚠[/yellow] No links to post.")
        return 0

    # Build post config
    post_config = PostConfig(
        header=args.header or "",
        footer=args.footer or "",
        continue_label=args.continue_label or "→ ادامه",
        max_links_per_line=args.links_per_line or 3,
    )

    # Show plan
    console.print(f"\n[bold]Creating post:[/bold]")
    console.print(f"  Links:     [cyan]{len(links)}[/cyan]")
    console.print(f"  Dest:      [cyan]{dest_channel}[/cyan]")
    console.print(f"  Cover:     [cyan]{args.cover or 'none'}[/cyan]")
    console.print(f"  Header:    [dim]{(args.header or '')[:60]!r}[/dim]")
    console.print(f"  Per line:  [cyan]{post_config.max_links_per_line}[/cyan]")

    # Start pool
    pool = AsyncBotPool(
        tokens=config.bot_tokens,
        api_id=config.api.api_id,
        api_hash=config.api.api_hash,
        session_dir=str(config.get_session_dir()),
    )

    console.print(f"\n[bold]Starting {pool.size} bots...[/bold]")
    await pool.start_all()

    if isinstance(dest_channel, int):
        await pool.resolve_peer_all(dest_channel)
        await pool.prime_destination_all(dest_channel)

    try:
        builder = PostBuilder(pool, config, db=get_db(config.get_db_path()))
        result = await builder.create_post(
            dest_channel=dest_channel,
            links=links,
            header=args.header or "",
            footer=args.footer or "",
            cover_path=args.cover,
            reply_to=args.reply_to,
            post_config=post_config,
        )

        if result.error:
            console.print(f"\n[red]✗[/red] Failed: {result.error}")
            await pool.stop_all()
            return 1

        console.print(f"\n[green]✓[/green] Post created!")
        console.print(f"  Main message:   [bold]{result.main_msg_id}[/bold]")
        console.print(f"  Continuations:  {result.continuation_msg_ids}")
        console.print(f"  Total parts:    {result.total_parts}")
        console.print(f"  Total links:    {result.total_links}")

        # Show links
        main_link = build_link(dest_channel, result.main_msg_id)
        console.print(f"\n  Main link: [cyan]{main_link}[/cyan]")

        # Log to operations_log
        db = get_db(config.get_db_path())
        store = Store(db)
        dest_db_id = stable_channel_db_id(dest_channel)
        if not store.channels.get(dest_db_id):
            store.channels.upsert(channel_id=dest_db_id, role="destination")
        store.ops.log(
            op_type="post_create",
            status="ok",
            dest_channel=dest_db_id,
            dest_msg_id=result.main_msg_id,
            meta_json=json.dumps({
                "total_parts": result.total_parts,
                "total_links": result.total_links,
                "continuation_msg_ids": result.continuation_msg_ids,
            }),
        )

        # Auto-sync DB
        if config.channels.db_sync and config.db.auto_sync:
            console.print(f"\n[bold]Auto-syncing DB...[/bold]")
            db_sync = DBSync(config, config.get_db_path())
            try:
                msg_id = db_sync.upload(description=f"Post created: {result.total_links} links")
                if msg_id:
                    console.print(f"[green]✓[/green] DB synced (msg_id={msg_id})")
            finally:
                db_sync.close()

        await pool.stop_all()
        return 0

    except Exception as e:
        console.print(f"\n[red]✗[/red] Error: {type(e).__name__}: {e}")
        logger.exception("Post creation failed")
        await pool.stop_all()
        return 1
