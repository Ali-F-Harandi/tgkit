"""PostBuilder — create linked-list caption messages in a channel.

THE PROBLEM:
    Telegram caption limits:
        - Media (photo/video/document) caption: 1024 chars
        - Text message: 4096 chars
    If you have 500 chapter links, they won't fit in one message.

THE SOLUTION (linked-list):
    Post A (cover + main caption): links to chapters 1-50, ends with "→ continue"
    Post B (reply to A): links to chapters 51-350, ends with "→ continue"
    Post C (reply to B): links to chapters 351-500

    Each post replies to the previous one, forming a linked list.
    The "→ continue" link points to the next message.

HOW IT WORKS:
    1. You provide a list of (label, url) pairs + optional cover image + header text
    2. PostBuilder splits links into parts that fit within the char limit
    3. Sends the first part (optionally with cover photo as media)
    4. Sends subsequent parts as replies, each linking to the next
    5. Edits earlier parts to add "→ continue" links once the next msg_id is known

USAGE:
    builder = PostBuilder(pool, config, db)
    result = await builder.create_post(
        dest_channel=-100...,
        header="📚 My Manga Collection\\n\\n📥 Download:",
        links=[("CH1", "https://t.me/c/.../1"), ("CH2", "https://t.me/c/.../2"), ...],
        cover_path="/path/to/cover.jpg",  # optional
        reply_to=None,  # optional: reply to an existing message
    )
    # result.main_msg_id, result.continuation_msg_ids
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyrogram.errors import FloodWait

from tgkit.config.schema import Config
from tgkit.transport.bot_pool import AsyncBotPool
from tgkit.transport.bot import Bot
from tgkit.models.link import build_link

logger = logging.getLogger(__name__)

# Telegram limits
MEDIA_CAPTION_LIMIT = 1024
TEXT_MESSAGE_LIMIT = 4096
# Safety margin for HTML entities, newlines, etc.
SAFETY_MARGIN = 200


@dataclass
class LinkItem:
    """A single link in a post."""
    label: str       # display text (e.g., "CH001", "Volume 1")
    url: str         # full URL (e.g., "https://t.me/c/123/456")

    def to_html(self) -> str:
        """Render as HTML link."""
        return f'• <a href="{self.url}">{self.label}</a>'


@dataclass
class PostConfig:
    """Configuration for a post."""
    header: str = ""                 # text before links (e.g., "📚 My Manga\\n\\n📥 Download:")
    footer: str = ""                 # text after links
    continue_label: str = "→ ادامه"  # label for the "continue" link
    separator: str = " - "           # separator between links on the same line
    max_links_per_line: int = 3      # how many links per line (separated by separator)
    use_text_messages: bool = True   # True: use text messages (4096 chars); False: use media captions (1024)


@dataclass
class PostResult:
    """Result of creating a post."""
    main_msg_id: int | None = None
    continuation_msg_ids: list[int] = field(default_factory=list)
    total_parts: int = 0
    total_links: int = 0
    error: str | None = None

    @property
    def all_msg_ids(self) -> list[int]:
        """All message IDs (main + continuations)."""
        ids = []
        if self.main_msg_id:
            ids.append(self.main_msg_id)
        ids.extend(self.continuation_msg_ids)
        return ids


# ============================================================================
# Link splitting logic
# ============================================================================

def split_links_into_parts(
    links: list[LinkItem],
    config: PostConfig,
    char_limit: int = TEXT_MESSAGE_LIMIT - SAFETY_MARGIN,
) -> list[list[LinkItem]]:
    """Split a list of links into parts that fit within char_limit.

    Each part is a list of LinkItem that can fit in one message.
    """
    if not links:
        return []

    parts: list[list[LinkItem]] = []
    current_part: list[LinkItem] = []
    current_size = 0

    # Estimate header/footer size (they go in the first part only)
    header_size = len(config.header) if config.header else 0
    footer_size = len(config.footer) if config.footer else 0
    continue_link_size = len(config.continue_label) + 50  # approx HTML overhead

    for i, link in enumerate(links):
        link_html = link.to_html()
        link_size = len(link_html) + len(config.separator) + 1  # +1 for newline

        # Check if adding this link would exceed the limit
        is_last_in_part = (i == len(links) - 1)

        # First part has header + footer + continue link
        if not current_part:
            available = char_limit - header_size - continue_link_size
            if not is_last_in_part:
                available -= footer_size
        else:
            available = char_limit - continue_link_size

        if current_size + link_size > available and current_part:
            # Start a new part
            parts.append(current_part)
            current_part = []
            current_size = 0
            # Recalculate available for new part (no header, but continue link)
            available = char_limit - continue_link_size
            if not is_last_in_part:
                available -= footer_size

        current_part.append(link)
        current_size += link_size

    if current_part:
        parts.append(current_part)

    return parts


def build_caption_parts(
    links: list[LinkItem],
    config: PostConfig,
    part_index: int,
    total_parts: int,
) -> str:
    """Build the caption/text for one part.

    Args:
        links: Links for this part
        config: PostConfig
        part_index: 0-based part number
        total_parts: Total number of parts

    Returns:
        HTML text for this message
    """
    lines: list[str] = []

    # Header (only on first part)
    if part_index == 0 and config.header:
        lines.append(config.header)
        lines.append("")

    # Links — group multiple per line
    for i in range(0, len(links), config.max_links_per_line):
        chunk = links[i:i + config.max_links_per_line]
        line = config.separator.join(link.to_html() for link in chunk)
        lines.append(line)

    # Continue link (if not the last part)
    if part_index < total_parts - 1:
        lines.append("")
        # The URL will be filled in after the next message is sent
        # For now, use a placeholder that will be edited later
        lines.append(f'• <a href="PLACEHOLDER_CONTINUE_URL">{config.continue_label}</a>')

    # Footer (only on last part)
    if part_index == total_parts - 1 and config.footer:
        lines.append("")
        lines.append(config.footer)

    return "\n".join(lines)


# ============================================================================
# PostBuilder
# ============================================================================

class PostBuilder:
    """Build linked-list caption messages in a channel.

    Usage:
        builder = PostBuilder(pool, config, db)
        result = await builder.create_post(
            dest_channel=-100...,
            links=[LinkItem("CH1", "https://..."), ...],
            header="📚 My Collection",
            cover_path="/path/to/cover.jpg",
        )
    """

    def __init__(self, pool: AsyncBotPool, config: Config, db=None):
        self.pool = pool
        self.config = config
        self.db = db

    async def create_post(
        self,
        dest_channel: int | str,
        links: list[LinkItem],
        header: str = "",
        footer: str = "",
        cover_path: str | None = None,
        reply_to: int | None = None,
        post_config: PostConfig | None = None,
    ) -> PostResult:
        """Create a linked-list post.

        Args:
            dest_channel: Destination channel ID
            links: List of LinkItem (label + url)
            header: Text before links (first message only)
            footer: Text after links (last message only)
            cover_path: Optional path to a cover image (sent as photo on first message)
            reply_to: Optional message ID to reply to (for the first message)
            post_config: Optional PostConfig (defaults to standard)

        Returns:
            PostResult with main_msg_id + continuation_msg_ids
        """
        if not links:
            return PostResult(error="no links provided")

        cfg = post_config or PostConfig(header=header, footer=footer)

        # Determine char limit based on whether we have a cover
        if cover_path:
            char_limit = MEDIA_CAPTION_LIMIT - SAFETY_MARGIN
        else:
            char_limit = TEXT_MESSAGE_LIMIT - SAFETY_MARGIN

        # Split links into parts
        parts = split_links_into_parts(links, cfg, char_limit)
        total_parts = len(parts)

        logger.info(
            f"PostBuilder: {len(links)} links → {total_parts} parts "
            f"(cover={'yes' if cover_path else 'no'}, limit={char_limit})"
        )

        result = PostResult(total_parts=total_parts, total_links=len(links))
        bot = await self.pool.get_next()

        try:
            # Send first part
            first_caption = build_caption_parts(parts[0], cfg, 0, total_parts)

            if cover_path:
                # Send as photo with caption
                first_msg = await bot.call(
                    lambda: bot.client.raw.send_photo(
                        chat_id=dest_channel,
                        photo=cover_path,
                        caption=first_caption[:MEDIA_CAPTION_LIMIT],
                        disable_notification=True,
                        reply_to_message_id=reply_to,
                    )
                )
            else:
                # Send as text message
                first_msg = await bot.call(
                    lambda: bot.client.raw.send_message(
                        chat_id=dest_channel,
                        text=first_caption,
                        disable_notification=True,
                        reply_to_message_id=reply_to,
                    )
                )

            result.main_msg_id = first_msg.id
            prev_msg_id = first_msg.id

            # Send continuation parts (as replies to the previous)
            for i in range(1, total_parts):
                part_caption = build_caption_parts(parts[i], cfg, i, total_parts)
                cont_msg = await bot.call(
                    lambda: bot.client.raw.send_message(
                        chat_id=dest_channel,
                        text=part_caption,
                        disable_notification=True,
                        reply_to_message_id=prev_msg_id,
                    )
                )
                result.continuation_msg_ids.append(cont_msg.id)

                # Edit the PREVIOUS message to replace PLACEHOLDER with actual continue link
                continue_url = build_link(dest_channel, cont_msg.id)
                await self._edit_continue_link(bot, dest_channel, prev_msg_id, continue_url, cfg)

                prev_msg_id = cont_msg.id

            logger.info(
                f"PostBuilder complete: main={result.main_msg_id}, "
                f"continuations={result.continuation_msg_ids}"
            )
            return result

        except FloodWait as e:
            logger.warning(f"PostBuilder FloodWait {e.value}s")
            result.error = f"FloodWait {e.value}s"
            return result

        except Exception as e:
            logger.error(f"PostBuilder failed: {type(e).__name__}: {e}")
            result.error = f"{type(e).__name__}: {e}"
            return result

    async def _edit_continue_link(
        self, bot: Bot, dest_channel: int | str,
        msg_id: int, continue_url: str, cfg: PostConfig,
    ) -> None:
        """Edit a message to replace the PLACEHOLDER continue link with the actual URL."""
        try:
            # Fetch the current message text
            msg = await bot.call(
                lambda: bot.client.raw.get_messages(dest_channel, msg_id)
            )
            if not msg or not msg.text:
                return

            # Replace placeholder
            old_text = msg.text
            new_text = old_text.replace("PLACEHOLDER_CONTINUE_URL", continue_url)

            if new_text != old_text:
                await bot.call(
                    lambda: bot.client.raw.edit_message_text(
                        chat_id=dest_channel,
                        message_id=msg_id,
                        text=new_text,
                    )
                )
        except Exception as e:
            logger.debug(f"Failed to edit continue link in msg {msg_id}: {e}")
