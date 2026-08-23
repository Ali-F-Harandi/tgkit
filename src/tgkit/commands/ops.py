"""`tgkit copy/forward/reupload` commands.

All three accept the same input specifiers:
    tgkit copy <link>                          # single message
    tgkit copy <link> --count N [--reverse]    # range
    tgkit copy --from-scan <id> [--filter ...] # from DB (the killer feature)
    tgkit copy --file links.txt                # from file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

from tgkit.config.schema import Config
from tgkit.config.loader import save_config
from tgkit.models.link import parse_link, parse_channel_ref
from tgkit.models.message import MessageRef, MessageInfo, MediaInfo
from tgkit.transport.bot_pool import AsyncBotPool
from tgkit.operations import CopyOp, ForwardOp, ReuploadOp, BatchRunner, Operation
from tgkit.operations.base import OpContext
from tgkit.db.connection import get_db
from tgkit.db.store import Store
from tgkit.db.sync import DBSync
from tgkit.capabilities import detect_capabilities, Capability
from tgkit.utils import stable_channel_db_id

logger = logging.getLogger(__name__)
console = Console()


# ============================================================================
# Build items list from various sources
# ============================================================================

def build_items_from_args(args, config: Config) -> list[MessageInfo]:
    """Build a list of MessageInfo from CLI args.

    Sources (in priority order):
        1. --from-scan <id> + optional --filter
        2. --file <path>
        3. <link> + optional --count
    """
    db = get_db(config.get_db_path())
    store = Store(db)

    # 1. From scan (the killer feature)
    if args.from_scan:
        scan_id = args.from_scan
        scan = store.scans.get(scan_id)
        if not scan:
            console.print(f"[red]✗[/red] Scan #{scan_id} not found.")
            return []

        # Build filters
        filters: dict[str, Any] = {}
        if args.filter:
            filters = parse_filter(args.filter)

        # Query messages from the scan
        rows = store.messages.query_by_scan(scan_id, filters=filters)

        if not rows:
            console.print(f"[yellow]⚠[/yellow] No messages found in scan #{scan_id} with given filters.")
            return []

        # Convert DB rows to MessageInfo
        items = []
        for row in rows:
            # Determine channel_id (int if numeric, else str for username)
            ch_id_raw = row["channel_id"]
            # For username-based channels stored as hash, we need the original
            # For now, use the hash — but we also stored channel in DB
            ch = store.channels.get(ch_id_raw)
            if ch and ch["username"]:
                channel_id = f"@{ch['username']}"
            else:
                channel_id = ch_id_raw

            ref = MessageRef(channel_id=channel_id, msg_id=row["msg_id"])
            media = MediaInfo(
                media_type=row["media_type"] or "none",
                file_id=row["file_id"] or "",
                file_name=row["file_name"] or "",
                file_extension=row["file_extension"] or "",
                file_size=row["file_size"] or 0,
                mime_type=row["mime_type"] or "",
                duration=row["duration"] or 0,
                width=row["width"] or 0,
                height=row["height"] or 0,
                has_thumb=bool(row["has_thumb"]),
            )
            info = MessageInfo(
                ref=ref,
                date=row["date"] or "",
                caption=row["caption"] or "",
                caption_length=row["caption"] and len(row["caption"]) or 0,
                is_marker=bool(row["is_marker"]),
                marker_emoji=row["marker_emoji"] or "",
                message_link=row["message_link"] or "",
                media=media,
            )
            items.append(info)

        console.print(f"[green]✓[/green] Loaded {len(items)} messages from scan #{scan_id}")
        return items

    # 2. From file
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
        links = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            links.append(line)

        items = []
        for link in links:
            try:
                channel_id, msg_id = parse_link(link)
                ref = MessageRef(channel_id=channel_id, msg_id=msg_id)
                # Create minimal MessageInfo (file_id will be fetched by the op)
                info = MessageInfo(ref=ref, message_link=link)
                items.append(info)
            except ValueError as e:
                console.print(f"[yellow]⚠[/yellow] Skipping invalid link: {link} ({e})")

        console.print(f"[green]✓[/green] Loaded {len(items)} links from {args.file}")
        return items

    # 3. Single link or range
    if args.link:
        try:
            channel_id, start_msg_id = parse_link(args.link)
        except ValueError as e:
            console.print(f"[red]✗[/red] {e}")
            return []

        if args.count:
            # Range mode
            if args.reverse:
                ids = list(range(start_msg_id, start_msg_id - args.count, -1))
            else:
                ids = list(range(start_msg_id, start_msg_id + args.count))

            items = []
            for mid in ids:
                ref = MessageRef(channel_id=channel_id, msg_id=mid)
                info = MessageInfo(ref=ref)
                items.append(info)

            console.print(f"[green]✓[/green] Generated {len(items)} message refs (range)")
            return items
        else:
            # Single
            ref = MessageRef(channel_id=channel_id, msg_id=start_msg_id)
            info = MessageInfo(ref=ref, message_link=args.link)
            return [info]

    console.print("[red]✗[/red] No input specified. Use <link>, --file, or --from-scan.")
    return []


def parse_filter(filter_str: str) -> dict[str, Any]:
    """Parse a filter expression into a dict.

    Examples:
        "ext=.zip" → {"file_extension": ".zip"}
        "ext=.zip size=>10MB" → {"file_extension": ".zip", "min_size": 10485760}
        "media=document" → {"media_type": "document"}
        "marker=true" → {"is_marker": True}
    """
    filters: dict[str, Any] = {}
    parts = filter_str.split()

    for part in parts:
        if "=" not in part:
            continue
        key, _, value = part.partition("=")

        if key == "ext":
            filters["file_extension"] = value
        elif key == "media":
            filters["media_type"] = value
        elif key == "size":
            # size=>10MB or size=<50MB or size=10MB-100MB
            if value.startswith(">"):
                filters["min_size"] = parse_size(value[1:])
            elif value.startswith("<"):
                filters["max_size"] = parse_size(value[1:])
            else:
                filters["min_size"] = parse_size(value)
        elif key == "marker":
            filters["is_marker"] = value.lower() in ("true", "1", "yes")

    return filters


def parse_size(s: str) -> int:
    """Parse a size string (e.g., '10MB', '500KB', '2GB') into bytes."""
    s = s.strip().upper()
    multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            return int(float(s[:-len(suffix)]) * mult)
    return int(s)


# ============================================================================
# Resolve destination channel
# ============================================================================

def resolve_dest_channel(args, config: Config) -> tuple[int | str, int] | None:
    """Resolve the destination channel from args or config.

    Returns (channel_id, db_id) or None if not set.
    """
    if args.to:
        channel_id = parse_channel_ref(args.to)
        db_id = stable_channel_db_id(channel_id)
        return channel_id, db_id

    if config.channels.default_destination:
        return config.channels.default_destination, config.channels.default_destination

    return None


# ============================================================================
# Main command handler (shared by copy/forward/reupload)
# ============================================================================

async def cmd_operation(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Handle copy/forward/reupload commands.

    The op_type is set in args._op_type by the CLI dispatcher.
    """
    op_type = args._op_type  # "copy" | "forward" | "reupload"

    # ── Capability check ──
    report = detect_capabilities(config)

    if op_type == "forward":
        if not report.can(Capability.FORWARD):
            console.print("[red]✗[/red] Forward requires at least 1 bot.")
            return 1
    else:
        # copy and reupload need MTProto (file_id / download)
        if not report.can(Capability.COPY_FILE_ID):
            console.print(f"[red]✗[/red] {op_type} requires Tier 2 (api_id + api_hash).")
            return 1

    if not config.bots:
        console.print("[red]✗[/red] No bots configured.")
        return 1

    # ── Resolve destination ──
    dest = resolve_dest_channel(args, config)
    if dest is None:
        console.print("[red]✗[/red] No destination channel specified.")
        console.print("    Use --to <channel> or set default_destination in config.")
        return 1

    dest_channel, dest_db_id = dest

    # ── Build items list ──
    items = build_items_from_args(args, config)
    if not items:
        console.print("[yellow]⚠[/yellow] No items to process.")
        return 0

    # ── Create operation ──
    if op_type == "copy":
        op = CopyOp(caption=args.caption)
    elif op_type == "forward":
        op = ForwardOp()
    elif op_type == "reupload":
        op = ReuploadOp(caption=args.caption)
    else:
        console.print(f"[red]✗[/red] Unknown operation: {op_type}")
        return 1

    # ── Show plan ──
    console.print(f"\n[bold]Operation:[/bold] {op_type}")
    console.print(f"  Items:     [cyan]{len(items)}[/cyan]")
    console.print(f"  Dest:      [cyan]{dest_channel}[/cyan]")
    console.print(f"  Parallel:  [cyan]{args.parallel or len(config.bots)}[/cyan]")
    if args.caption is not None and op_type in ("copy", "reupload"):
        preview = args.caption[:60] + ("..." if len(args.caption) > 60 else "")
        console.print(f"  Caption:   [dim]{preview!r}[/dim]")

    # ── Open DB ──
    db = get_db(config.get_db_path())

    # Ensure dest channel exists in DB (for FK)
    store = Store(db)
    if not store.channels.get(dest_db_id):
        store.channels.upsert(
            channel_id=dest_db_id,
            username=dest_channel.lstrip("@") if isinstance(dest_channel, str) else None,
            role="destination",
        )

    # ── Build skip set (resume) ──
    skip_msg_ids: set[int] = set()
    if not args.fresh:
        # Find items already copied/forwarded to this dest
        from tgkit.db.store import OperationsLogStore
        ops_store = OperationsLogStore(db)
        # Query recent successful ops of this type to this dest
        recent = db.query_all(
            """SELECT source_msg_id FROM operations_log
               WHERE op_type = ? AND status = 'ok' AND dest_channel = ?
               ORDER BY started_at DESC LIMIT 10000""",
            (op_type, dest_db_id)
        )
        skip_msg_ids = {r["source_msg_id"] for r in recent if r["source_msg_id"]}
        if skip_msg_ids:
            console.print(f"  [dim]Resume: skipping {len(skip_msg_ids)} already-processed items[/dim]")

    # ── Start bot pool ──
    pool = AsyncBotPool(
        tokens=config.bot_tokens,
        api_id=config.api.api_id,
        api_hash=config.api.api_hash,
        session_dir=str(config.get_session_dir()),
    )

    console.print(f"\n[bold]Starting {pool.size} bots...[/bold]")
    await pool.start_all()

    # Resolve dest peer if numeric
    if isinstance(dest_channel, int):
        await pool.resolve_peer_all(dest_channel)
        await pool.prime_destination_all(dest_channel)

    # ── Build context ──
    ctx = OpContext(
        pool=pool,
        config=config,
        db=db,
        dest_channel=dest_channel,
        dest_channel_db_id=dest_db_id,
    )

    # ── Run batch ──
    parallel = args.parallel or len(config.bots)
    runner = BatchRunner(op=op, parallel=parallel, skip_msg_ids=skip_msg_ids)

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("({task.completed}/{task.total})"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"{op_type}ing...", total=len(items))

            def on_progress(done: int, total: int, result: OpResult) -> None:
                progress.update(task, completed=done)
                if not result.ok and result.error:
                    console.print(
                        f"  [red]✗[/red] msg {result.source_ref.msg_id if result.source_ref else '?'}: "
                        f"{result.error[:80]}",
                        style="dim",
                    )

            batch_result = await runner.run(items, ctx, on_progress=on_progress)

        # ── Print summary ──
        console.print()
        console.print(f"[green]✓[/green] {op_type.title()} complete!")
        console.print(f"  Success: [bold green]{batch_result.success_count}[/bold green]")
        console.print(f"  Failed:  [bold red]{batch_result.failure_count}[/bold red]")
        console.print(f"  Time:    {batch_result.elapsed_seconds:.1f}s")
        console.print(f"  Rate:    {batch_result.effective_rate:.1f} items/s")

        # Show some results
        if batch_result.results:
            console.print(f"\n[bold]Sample results:[/bold]")
            for r in batch_result.results[:5]:
                if r.ok:
                    console.print(f"  [green]✓[/green] msg {r.source_ref.msg_id} → {r.share_link}")
                else:
                    console.print(f"  [red]✗[/red] msg {r.source_ref.msg_id if r.source_ref else '?'}: {r.error}")

        # ── Save results if requested ──
        if args.save_results:
            out_path = Path(args.save_results)
            out_path.write_text(
                json.dumps(
                    [r.to_dict() for r in batch_result.results],
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            console.print(f"\n[green]✓[/green] Results saved: [bold]{out_path}[/bold]")

        # ── Auto-sync DB ──
        if config.channels.db_sync and config.db.auto_sync:
            console.print(f"\n[bold]Auto-syncing DB...[/bold]")
            db_sync = DBSync(config, config.get_db_path())
            try:
                msg_id = db_sync.upload(description=f"{op_type} batch: {batch_result.success_count} items")
                if msg_id:
                    console.print(f"[green]✓[/green] DB synced (msg_id={msg_id})")
            finally:
                db_sync.close()

        await pool.stop_all()

        return 0 if batch_result.failure_count == 0 else 1

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠[/yellow] Interrupted — partial results saved to DB.")
        await pool.stop_all()
        return 130

    except Exception as e:
        console.print(f"\n[red]✗[/red] Error: {type(e).__name__}: {e}")
        logger.exception("Operation failed")
        await pool.stop_all()
        return 1
