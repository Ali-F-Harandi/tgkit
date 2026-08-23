"""ReuploadOp — download from source + upload fresh (breaks copyright linkage).

Downloads the file from the source channel to a temp dir, then uploads it
as a new file to the destination. The new file gets a NEW file_id, so
there's no link to the source. If the source is DMCA'd, the copy survives.

When to use:
    - You want to break copyright linkage
    - You want to rename the file (re-upload with new filename)
    - The file_id approach doesn't work

When NOT to use:
    - Speed matters (this is the slowest: download + upload)
    - File is large (> 2 GB — would need chunking)

Cost: ~10-50x slower than CopyOp depending on file size.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from pyrogram.errors import FloodWait

from tgkit.operations.base import Operation, OpContext, OpResult
from tgkit.models.message import MessageInfo

logger = logging.getLogger(__name__)


class ReuploadOp(Operation):
    """Download from source + upload fresh (breaks copyright linkage)."""

    @property
    def name(self) -> str:
        return "reupload"

    def __init__(self, caption: str | None = None, temp_dir: str = "/tmp/tgkit_reupload"):
        """
        Args:
            caption: Custom caption (None = keep original, "" = no caption)
            temp_dir: Temp directory for downloads
        """
        self.caption = caption
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    async def run(self, item: MessageInfo, ctx: OpContext) -> OpResult:
        """Download + reupload the message."""
        bot = await self.get_bot(ctx)

        # Determine caption
        if self.caption is None:
            caption = item.caption
        else:
            caption = self.caption

        # Determine filename
        file_name = item.media.file_name or f"file_{item.ref.msg_id}"
        download_path = self.temp_dir / file_name

        try:
            # Phase 1: Download from source
            result = await self._download(bot, item, download_path)
            if not result.ok:
                self.log_operation(ctx, item, result, bot_id=bot.bot_id)
                return result

            # Phase 2: Upload fresh to destination
            result = await self._upload(bot, ctx, item, download_path, caption)
            if result.ok:
                # Clean up temp file
                try:
                    download_path.unlink()
                except OSError:
                    pass

            self.log_operation(ctx, item, result, bot_id=bot.bot_id)
            return result

        except FloodWait as e:
            result = OpResult(
                ok=False,
                error=f"FloodWait {e.value}s",
                source_ref=item.ref,
            )
            self.log_operation(ctx, item, result, bot_id=bot.bot_id)
            return result

        except Exception as e:
            logger.error(f"ReuploadOp failed on msg {item.ref.msg_id}: {type(e).__name__}: {e}")
            result = OpResult(
                ok=False,
                error=f"{type(e).__name__}: {e}",
                source_ref=item.ref,
            )
            self.log_operation(ctx, item, result, bot_id=bot.bot_id)
            return result

    async def _download(self, bot, item: MessageInfo, download_path: Path) -> OpResult:
        """Download the file from the source channel."""
        try:
            # Fetch the source message to get a fresh reference
            msg = await bot.client.raw.get_messages(
                item.ref.channel_id, item.ref.msg_id
            )
            if msg is None or getattr(msg, "empty", False):
                return OpResult(
                    ok=False,
                    error="source message not found",
                    source_ref=item.ref,
                )

            # Download to temp path
            await bot.call(
                lambda: bot.client.raw.download_media(msg, file_name=str(download_path))
            )

            if not download_path.exists():
                return OpResult(
                    ok=False,
                    error="download failed — file not created",
                    source_ref=item.ref,
                )

            return OpResult(ok=True, source_ref=item.ref)

        except Exception as e:
            return OpResult(
                ok=False,
                error=f"download: {type(e).__name__}: {e}",
                source_ref=item.ref,
            )

    async def _upload(
        self, bot, ctx: OpContext, item: MessageInfo,
        file_path: Path, caption: str,
    ) -> OpResult:
        """Upload the file fresh to the destination."""
        try:
            cap = caption[:1024] if caption else ""
            media = item.media

            # Choose upload method based on media type
            result = await bot.call(
                lambda: self._upload_by_type(bot, ctx.dest_channel, file_path, media, cap)
            )

            if result is None:
                return OpResult(
                    ok=False,
                    error="upload returned None",
                    source_ref=item.ref,
                )

            from tgkit.models.link import build_link
            share_link = build_link(ctx.dest_channel, result.id)

            return OpResult(
                ok=True,
                new_msg_id=result.id,
                share_link=share_link,
                media_type=media.media_type,
                file_name=media.file_name,
                file_size=media.file_size,
                source_ref=item.ref,
            )

        except Exception as e:
            return OpResult(
                ok=False,
                error=f"upload: {type(e).__name__}: {e}",
                source_ref=item.ref,
            )

    async def _upload_by_type(self, bot, chat_id, file_path: Path, media, caption: str):
        """Upload file by media type."""
        path_str = str(file_path)

        if media.media_type == "document":
            return await bot.client.raw.send_document(
                chat_id=chat_id,
                document=path_str,
                caption=caption,
                disable_notification=True,
            )
        elif media.media_type == "video":
            return await bot.client.raw.send_video(
                chat_id=chat_id,
                video=path_str,
                caption=caption,
                duration=media.duration or 0,
                width=media.width or 0,
                height=media.height or 0,
                disable_notification=True,
            )
        elif media.media_type == "photo":
            return await bot.client.raw.send_photo(
                chat_id=chat_id,
                photo=path_str,
                caption=caption,
                disable_notification=True,
            )
        elif media.media_type == "audio":
            return await bot.client.raw.send_audio(
                chat_id=chat_id,
                audio=path_str,
                caption=caption,
                duration=media.duration or 0,
                disable_notification=True,
            )
        elif media.media_type == "animation":
            return await bot.client.raw.send_animation(
                chat_id=chat_id,
                animation=path_str,
                caption=caption,
                duration=media.duration or 0,
                width=media.width or 0,
                height=media.height or 0,
                disable_notification=True,
            )
        else:
            # Default: send as document
            return await bot.client.raw.send_document(
                chat_id=chat_id,
                document=path_str,
                caption=caption,
                disable_notification=True,
            )
