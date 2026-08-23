"""CopyOp — copy via file_id (NO forward header, fully editable, instant).

Uses pyrofork's send_document(file_id) / send_video(file_id) / etc.
The file_id is a server-side reference — Telegram copies the file from
its CDN to the destination. No download, no re-upload, instant.

When to use:
    - You want NO "Forwarded from" header
    - You want to add a custom caption
    - You want the message to be editable
    - Speed: instant (server-side copy)

When NOT to use:
    - You want to break copyright linkage → use ReuploadOp
    - file_id is stale → re-fetch or use ReuploadOp

NOTE: file_id is per-bot and can expire. If the stored file_id fails,
CopyOp will re-fetch a fresh file_id from the source channel and retry.
"""

from __future__ import annotations

import logging
from typing import Optional

from pyrogram.errors import FloodWait

from tgkit.operations.base import Operation, OpContext, OpResult
from tgkit.models.message import MessageInfo

logger = logging.getLogger(__name__)


class CopyOp(Operation):
    """Copy a message via file_id (NO forward header, editable, instant)."""

    @property
    def name(self) -> str:
        return "copy"

    def __init__(self, caption: str | None = None):
        """
        Args:
            caption: Custom caption (None = keep original).
                     Empty string "" = no caption.
        """
        self.caption = caption

    async def run(self, item: MessageInfo, ctx: OpContext) -> OpResult:
        """Copy the message to ctx.dest_channel via file_id."""
        bot = await self.get_bot(ctx)

        # Determine caption
        if self.caption is None:
            caption = item.caption  # keep original
        else:
            caption = self.caption  # use custom (could be "")

        # If no file_id, fetch fresh from source
        file_id = item.media.file_id
        if not file_id:
            logger.debug(f"No stored file_id for msg {item.ref.msg_id}, fetching fresh...")
            file_id = await self._fetch_fresh_file_id(bot, item)
            if not file_id:
                result = OpResult(
                    ok=False,
                    error="could not fetch file_id from source",
                    source_ref=item.ref,
                    media_type=item.media.media_type,
                    file_name=item.media.file_name,
                    file_size=item.media.file_size,
                )
                self.log_operation(ctx, item, result, bot_id=bot.bot_id)
                return result

        # Try to send
        result = await self._try_send(bot, ctx, item, file_id, caption)

        if result.ok:
            self.log_operation(ctx, item, result, bot_id=bot.bot_id)
            return result

        # If file_id was stale, re-fetch from source and retry
        if self._is_stale_file_id_error(result.error):
            logger.debug(f"file_id stale for msg {item.ref.msg_id}, re-fetching...")
            fresh_file_id = await self._fetch_fresh_file_id(bot, item)
            if fresh_file_id and fresh_file_id != file_id:
                result = await self._try_send(bot, ctx, item, fresh_file_id, caption)
                if result.ok:
                    self.log_operation(ctx, item, result, bot_id=bot.bot_id)
                    return result

        # Final failure
        self.log_operation(ctx, item, result, bot_id=bot.bot_id)
        return result

    async def _try_send(
        self, bot, ctx: OpContext, item: MessageInfo,
        file_id: str, caption: str,
    ) -> OpResult:
        """Try to send via file_id. Returns OpResult."""
        if not file_id:
            return OpResult(
                ok=False,
                error="no file_id available",
                source_ref=item.ref,
                media_type=item.media.media_type,
                file_name=item.media.file_name,
                file_size=item.media.file_size,
            )

        try:
            # Truncate caption to 1024 chars (Telegram media caption limit)
            cap = caption[:1024] if caption else ""

            # Choose send method based on media type
            result = await bot.call(
                lambda: self._send_by_type(bot, ctx.dest_channel, file_id, item, cap)
            )

            if result is None:
                return OpResult(
                    ok=False,
                    error="send returned None",
                    source_ref=item.ref,
                )

            # Build share link
            from tgkit.models.link import build_link
            share_link = build_link(ctx.dest_channel, result.id)

            return OpResult(
                ok=True,
                new_msg_id=result.id,
                share_link=share_link,
                media_type=item.media.media_type,
                file_name=item.media.file_name,
                file_size=item.media.file_size,
                source_ref=item.ref,
            )

        except FloodWait as e:
            return OpResult(
                ok=False,
                error=f"FloodWait {e.value}s",
                source_ref=item.ref,
            )
        except Exception as e:
            return OpResult(
                ok=False,
                error=f"{type(e).__name__}: {e}",
                source_ref=item.ref,
            )

    async def _send_by_type(self, bot, chat_id, file_id, item: MessageInfo, caption: str):
        """Send file by media type using the appropriate pyrofork method."""
        media = item.media

        if media.media_type == "document":
            return await bot.client.raw.send_document(
                chat_id=chat_id,
                document=file_id,
                caption=caption,
                disable_notification=True,
            )
        elif media.media_type == "video":
            return await bot.client.raw.send_video(
                chat_id=chat_id,
                video=file_id,
                caption=caption,
                duration=media.duration or 0,
                width=media.width or 0,
                height=media.height or 0,
                disable_notification=True,
            )
        elif media.media_type == "photo":
            return await bot.client.raw.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=caption,
                disable_notification=True,
            )
        elif media.media_type == "audio":
            return await bot.client.raw.send_audio(
                chat_id=chat_id,
                audio=file_id,
                caption=caption,
                duration=media.duration or 0,
                disable_notification=True,
            )
        elif media.media_type == "animation":
            return await bot.client.raw.send_animation(
                chat_id=chat_id,
                animation=file_id,
                caption=caption,
                duration=media.duration or 0,
                width=media.width or 0,
                height=media.height or 0,
                disable_notification=True,
            )
        elif media.media_type == "voice":
            return await bot.client.raw.send_voice(
                chat_id=chat_id,
                voice=file_id,
                caption=caption,
                disable_notification=True,
            )
        elif media.media_type == "video_note":
            return await bot.client.raw.send_video_note(
                chat_id=chat_id,
                video_note=file_id,
                duration=media.duration or 0,
                length=media.width or 0,
                disable_notification=True,
            )
        elif media.media_type == "text":
            return await bot.client.raw.send_message(
                chat_id=chat_id,
                text=item.caption or "",
                disable_notification=True,
            )
        else:
            raise ValueError(f"Unsupported media type: {media.media_type}")

    def _is_stale_file_id_error(self, error: str | None) -> bool:
        """Check if an error indicates a stale/invalid file_id."""
        if not error:
            return False
        error_lower = error.lower()
        return any(p in error_lower for p in [
            "media_empty", "media_invalid", "file_id_invalid",
            "file_id_empty", "bad_request",
        ])

    async def _fetch_fresh_file_id(self, bot, item: MessageInfo) -> str | None:
        """Re-fetch file_id from the source channel. Also updates item.media."""
        try:
            msg = await bot.call(
                lambda: bot.client.raw.get_messages(
                    item.ref.channel_id, item.ref.msg_id
                )
            )
            if msg is None or getattr(msg, "empty", False):
                return None

            # Extract fresh media info
            from tgkit.scan.extract import extract_message_info
            fresh_info = extract_message_info(msg)

            # Update item's media info
            if fresh_info.media.file_id:
                item.media = fresh_info.media
                if not item.caption and fresh_info.caption:
                    item.caption = fresh_info.caption
                if not item.message_link and fresh_info.message_link:
                    item.message_link = fresh_info.message_link
                if not item.date and fresh_info.date:
                    item.date = fresh_info.date

            return fresh_info.media.file_id or None

        except Exception as e:
            logger.debug(f"Failed to re-fetch file_id for msg {item.ref.msg_id}: {e}")
            return None
