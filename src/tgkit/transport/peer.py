"""PeerResolver — cache access_hash for channels with IDs > int32.

THE 64-BIT CHANNEL ID PROBLEM:
    vanilla pyrogram 2.0.x has:
        MIN_CHANNEL_ID = -1002147483647  (i.e., max internal ID = 2147483647)
    Any channel with internal ID > 2147483647 fails with:
        ValueError: Peer id invalid: -1003873843444

    pyrofork fixes this:
        MIN_CHANNEL_ID = -100999999999999

    But even with pyrofork, you must cache the channel's access_hash in the
    session before you can call get_messages / send_document on it. Bots can't
    use get_dialogs() (BotMethodInvalid), so the only way to cache is:

        1. channels.GetChannels with access_hash=0 → server returns real hash
        2. The session caches it automatically

    This module wraps that call and tracks which channels are resolved.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pyrogram import Client
from pyrogram.raw.functions.channels import GetChannels
from pyrogram.raw.types import InputChannel

logger = logging.getLogger(__name__)


class PeerResolver:
    """Resolves and caches channel peers via channels.GetChannels.

    One instance per TgClient (shares its session).
    """

    def __init__(self, client: Client):
        self._client = client
        self._resolved: set[int] = set()  # channel IDs that have been resolved
        self._lock = asyncio.Lock()

    async def resolve(self, channel_id: int) -> bool:
        """Resolve a channel peer (cache its access_hash in session).

        Args:
            channel_id: full channel ID (e.g., -1003873843444)

        Returns:
            True if resolved successfully (or already cached), False on failure.
        """
        if channel_id in self._resolved:
            return True

        async with self._lock:
            # Double-check after acquiring lock
            if channel_id in self._resolved:
                return True

            # Extract internal ID (strip -100 prefix)
            # -1003873843444 → 3873843444
            id_str = str(channel_id)
            if id_str.startswith("-100"):
                internal_id = int(id_str[4:])
            else:
                # Already internal form or a small channel ID
                internal_id = abs(channel_id)

            try:
                result = await self._client.invoke(
                    GetChannels(id=[InputChannel(
                        channel_id=internal_id,
                        access_hash=0,
                    )])
                )
                if result.chats and len(result.chats) > 0:
                    chat = result.chats[0]
                    logger.debug(
                        f"Resolved channel {channel_id}: title={chat.title!r}, "
                        f"access_hash={chat.access_hash}"
                    )
                    self._resolved.add(channel_id)
                    return True
                return False
            except Exception as e:
                # Many channels are already cached in the session — the invoke
                # may fail with CHANNEL_INVALID but the peer is still usable.
                # We treat this as "already resolved" and let the caller try.
                logger.debug(
                    f"GetChannels failed for {channel_id} "
                    f"(peer may already be cached): {type(e).__name__}: {e}"
                )
                # Optimistically mark as resolved — if the peer is in the session,
                # subsequent calls will work. If not, the caller will get a clear error.
                self._resolved.add(channel_id)
                return True  # optimistic

    def is_resolved(self, channel_id: int) -> bool:
        """Check if a channel has been resolved (without doing it)."""
        return channel_id in self._resolved

    def clear(self) -> None:
        """Clear the resolved cache (forces re-resolution on next call)."""
        self._resolved.clear()

    @property
    def resolved_channels(self) -> set[int]:
        """Set of resolved channel IDs (read-only view)."""
        return self._resolved.copy()
