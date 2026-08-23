"""ForwardOp — native Telegram forward (preserves 'Forwarded from' header).

Uses pyrofork's forward_messages(). The destination message will show
"Forwarded from <source>" — NOT editable as a clean message.

When to use:
    - You want to preserve attribution
    - You don't need to edit the caption
    - Speed: instant (no file transfer)

When NOT to use:
    - You want no forward header → use CopyOp
    - You want custom caption → use CopyOp
    - You want to break copyright linkage → use ReuploadOp
"""

from __future__ import annotations

import logging

from pyrogram.errors import FloodWait

from tgkit.operations.base import Operation, OpContext, OpResult
from tgkit.models.message import MessageInfo

logger = logging.getLogger(__name__)


class ForwardOp(Operation):
    """Forward a message (WITH 'Forwarded from' header)."""

    @property
    def name(self) -> str:
        return "forward"

    async def run(self, item: MessageInfo, ctx: OpContext) -> OpResult:
        """Forward the message to ctx.dest_channel."""
        bot = await self.get_bot(ctx)

        try:
            # Use bot.call (serialized, throttled) for write operations
            result = await bot.call(
                lambda: bot.client.raw.forward_messages(
                    chat_id=ctx.dest_channel,
                    from_chat_id=item.ref.channel_id,
                    message_ids=item.ref.msg_id,
                    disable_notification=True,
                )
            )

            # forward_messages returns a list (or single Message)
            if isinstance(result, list):
                msg = result[0] if result else None
            else:
                msg = result

            if msg is None:
                return OpResult(
                    ok=False,
                    error="forward_messages returned None",
                    source_ref=item.ref,
                )

            # Build share link
            from tgkit.models.link import build_link
            if isinstance(ctx.dest_channel, int):
                share_link = build_link(ctx.dest_channel, msg.id)
            else:
                share_link = build_link(ctx.dest_channel, msg.id)

            result_obj = OpResult(
                ok=True,
                new_msg_id=msg.id,
                share_link=share_link,
                media_type=item.media.media_type,
                file_name=item.media.file_name,
                file_size=item.media.file_size,
                source_ref=item.ref,
            )

            # Log
            self.log_operation(ctx, item, result_obj, bot_id=bot.bot_id)
            return result_obj

        except FloodWait as e:
            # Bot.call already handles FloodWait, but if it propagates:
            logger.warning(f"ForwardOp FloodWait {e.value}s on msg {item.ref.msg_id}")
            result = OpResult(
                ok=False,
                error=f"FloodWait {e.value}s",
                source_ref=item.ref,
            )
            self.log_operation(ctx, item, result, bot_id=bot.bot_id)
            return result

        except Exception as e:
            logger.error(f"ForwardOp failed on msg {item.ref.msg_id}: {type(e).__name__}: {e}")
            result = OpResult(
                ok=False,
                error=f"{type(e).__name__}: {e}",
                source_ref=item.ref,
            )
            self.log_operation(ctx, item, result, bot_id=bot.bot_id)
            return result
