"""`tgkit delete` — delete messages from a channel.

Supports:
    - Single message: tgkit delete <channel>/<msg_id>
    - Multiple: tgkit delete <channel>/<msg_id> --count N
    - From file: tgkit delete --file msg_ids.txt
    - From operations_log: tgkit delete --from-log --op-type copy --status ok
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from rich.console import Console

from tgkit.config.schema import Config
from tgkit.transport.bot_api import BotAPIClient, BotAPIError
from tgkit.db.connection import get_db
from tgkit.db.store import Store
from tgkit.db.sync import DBSync
from tgkit.capabilities import detect_capabilities, Capability
from tgkit.models.link import parse_channel_ref
from tgkit.utils import stable_channel_db_id

logger = logging.getLogger(__name__)
console = Console()


async def cmd_delete(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Delete messages from a channel."""
    report = detect_capabilities(config)
    if not report.can(Capability.DELETE):
        console.print("[red]✗[/red] Delete requires at least 1 bot.")
        return 1

    if not config.bots:
        console.print("[red]✗[/red] No bots configured.")
        return 1

    # Build list of (channel, msg_id) pairs to delete
    targets: list[tuple[int | str, int]] = []

    if args.from_log:
        # Delete from operations_log
        db = get_db(config.get_db_path())
        op_type = args.op_type or "copy"
        status = args.status or "ok"
        rows = db.query_all(
            """SELECT dest_channel, dest_msg_id FROM operations_log
               WHERE op_type = ? AND status = ? AND dest_msg_id IS NOT NULL
               ORDER BY started_at DESC""",
            (op_type, status)
        )
        for r in rows:
            if r["dest_channel"] and r["dest_msg_id"]:
                targets.append((r["dest_channel"], r["dest_msg_id"]))
        console.print(f"[green]✓[/green] Found {len(targets)} messages from operations_log ({op_type}/{status})")

    elif args.file:
        # From file: one ref per line (channel/msg_id)
        text = Path(args.file).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "/" in line:
                ch_str, _, mid_str = line.partition("/")
                channel = parse_channel_ref(ch_str)
                msg_id = int(mid_str)
                targets.append((channel, msg_id))
        console.print(f"[green]✓[/green] Loaded {len(targets)} targets from {args.file}")

    elif args.channel and args.msg_id:
        # Single or range from --channel + --msg-id
        channel = parse_channel_ref(args.channel)
        start_msg_id = args.msg_id

        if args.count:
            # Range: delete N consecutive messages
            for i in range(args.count):
                targets.append((channel, start_msg_id + i))
        else:
            targets.append((channel, start_msg_id))

    if not targets:
        console.print("[yellow]⚠[/yellow] No messages to delete.")
        return 0

    console.print(f"\n[bold]Deleting {len(targets)} message(s)...[/bold]")

    bot_api = BotAPIClient(config.bots[0].token)
    db = get_db(config.get_db_path())
    store = Store(db)

    success_count = 0
    failure_count = 0

    try:
        # Group by channel for batch delete
        by_channel: dict[int | str, list[int]] = {}
        for channel, msg_id in targets:
            by_channel.setdefault(channel, []).append(msg_id)

        for channel, msg_ids in by_channel.items():
            console.print(f"  [dim]Channel {channel}: {len(msg_ids)} messages[/dim]")

            # Delete in batches of 100 (Bot API limit)
            for i in range(0, len(msg_ids), 100):
                batch = msg_ids[i:i+100]
                try:
                    if len(batch) == 1:
                        bot_api.delete_message(channel, batch[0])
                    else:
                        bot_api.delete_messages(channel, batch)
                    success_count += len(batch)
                    console.print(f"    [green]✓[/green] Deleted {len(batch)} messages")
                except BotAPIError as e:
                    failure_count += len(batch)
                    console.print(f"    [red]✗[/red] Failed: {e}")

            # Log each deletion
            channel_db_id = stable_channel_db_id(channel)
            if not store.channels.get(channel_db_id):
                store.channels.upsert(channel_id=channel_db_id, role="destination")

            for mid in msg_ids:
                store.ops.log(
                    op_type="delete",
                    status="ok" if mid in msg_ids[:success_count] else "failed",
                    dest_channel=channel_db_id,
                    dest_msg_id=mid,
                    bot_id=config.bots[0].bot_id,
                )

        console.print(f"\n[green]✓[/green] Delete complete:")
        console.print(f"  Success: [bold green]{success_count}[/bold green]")
        console.print(f"  Failed:  [bold red]{failure_count}[/bold red]")

        # Auto-sync DB
        if config.channels.db_sync and config.db.auto_sync:
            db_sync = DBSync(config, config.get_db_path())
            try:
                db_sync.upload(description=f"Deleted {success_count} messages")
            finally:
                db_sync.close()

        return 0 if failure_count == 0 else 1

    finally:
        bot_api.close()
