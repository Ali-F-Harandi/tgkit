"""`tgkit edit` — edit message caption/text/media in-place.

Uses Bot API editMessageCaption/editMessageText/editMessageMedia.
These preserve the message_id, so all references (links, reply chains) stay valid.

Works in both Tier 1 (Bot API) and Tier 2 (MTProto).
"""

from __future__ import annotations

import argparse
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


async def cmd_edit(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Edit a message's caption, text, or media in-place.

    Examples:
        tgkit edit <channel>/<msg_id> --caption "New caption"
        tgkit edit <channel>/<msg_id> --text "New text"
        tgkit edit <channel>/<msg_id> --media /path/to/new_file.jpg
    """
    report = detect_capabilities(config)
    if not report.can(Capability.EDIT_CAPTION):
        console.print("[red]✗[/red] Edit requires at least 1 bot.")
        return 1

    if not config.bots:
        console.print("[red]✗[/red] No bots configured.")
        return 1

    # Parse channel/msg_id from --channel and --msg-id
    channel = parse_channel_ref(args.channel)
    msg_id = args.msg_id

    if not msg_id:
        console.print("[red]✗[/red] Message ID required. Use --msg-id.")
        return 1

    # Must specify what to edit
    if not any([args.caption is not None, args.text is not None, args.media]):
        console.print("[red]✗[/red] Specify --caption, --text, or --media")
        return 1

    console.print(f"[bold]Editing message:[/bold] {channel}/{msg_id}")

    # Use Bot API for edits (works in both tiers, simpler than MTProto)
    bot_api = BotAPIClient(config.bots[0].token)
    db = get_db(config.get_db_path())
    store = Store(db)

    try:
        # Edit caption (for media messages)
        if args.caption is not None:
            console.print(f"  [dim]Editing caption...[/dim]")
            try:
                bot_api.edit_message_caption(
                    chat_id=channel,
                    message_id=msg_id,
                    caption=args.caption,
                )
                console.print(f"[green]✓[/green] Caption updated")
            except BotAPIError as e:
                if "message not modified" in str(e).lower():
                    console.print(f"[yellow]⚠[/yellow] Caption unchanged (same content)")
                else:
                    raise

        # Edit text (for text messages)
        if args.text is not None:
            console.print(f"  [dim]Editing text...[/dim]")
            try:
                bot_api.edit_message_text(
                    chat_id=channel,
                    message_id=msg_id,
                    text=args.text,
                )
                console.print(f"[green]✓[/green] Text updated")
            except BotAPIError as e:
                if "message not modified" in str(e).lower():
                    console.print(f"[yellow]⚠[/yellow] Text unchanged (same content)")
                else:
                    raise

        # Edit media (replace file in-place)
        if args.media:
            console.print(f"  [dim]Replacing media...[/dim]")
            media_type = args.media_type or "document"
            bot_api.edit_message_media(
                chat_id=channel,
                message_id=msg_id,
                media_path=args.media,
                media_type=media_type,
            )
            console.print(f"[green]✓[/green] Media replaced (msg_id preserved: {msg_id})")

        # Log operation
        channel_db_id = stable_channel_db_id(channel)
        if not store.channels.get(channel_db_id):
            store.channels.upsert(channel_id=channel_db_id, role="destination")
        store.ops.log(
            op_type="edit",
            status="ok",
            dest_channel=channel_db_id,
            dest_msg_id=msg_id,
            bot_id=config.bots[0].bot_id,
            meta_json=__import__("json").dumps({
                "caption": args.caption is not None,
                "text": args.text is not None,
                "media": args.media or "",
            }),
        )

        # Auto-sync DB
        if config.channels.db_sync and config.db.auto_sync:
            db_sync = DBSync(config, config.get_db_path())
            try:
                db_sync.upload(description=f"Edit: {channel}/{msg_id}")
            finally:
                db_sync.close()

        return 0

    except BotAPIError as e:
        console.print(f"[red]✗[/red] Edit failed: {e}")
        store.ops.log(
            op_type="edit",
            status="failed",
            dest_channel=stable_channel_db_id(channel),
            dest_msg_id=msg_id,
            bot_id=config.bots[0].bot_id,
            error=str(e),
        )
        return 1

    finally:
        bot_api.close()
