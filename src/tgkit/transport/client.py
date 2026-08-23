"""TgClient — thin wrapper around a single pyrofork Client.

Owns:
    - pyrofork Client (one per bot)
    - PeerResolver (caches access_hash for 64-bit channel IDs)
    - session file at ~/.tgkit/sessions/bot_<id>.session

Does NOT own:
    - throttle (that's in Bot)
    - pool (that's in AsyncBotPool)

Usage:
    client = TgClient(token="...", api_id=..., api_hash=..., session_dir=...)
    await client.start()
    # Use client.raw for any pyrofork method:
    msg = await client.raw.get_messages("@channel", 123)
    await client.stop()
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from pyrogram import Client
from pyrogram.enums import ParseMode

from tgkit.transport.peer import PeerResolver

logger = logging.getLogger(__name__)


class TgClient:
    """A single pyrofork client for one bot token.

    Attributes:
        token: Bot token from BotFather
        bot_id: Numeric bot ID (extracted from token)
        session_name: Full path to session file (without .session extension)
        raw: The underlying pyrofork Client (use for direct API calls)
        peer: PeerResolver for this client
    """

    def __init__(
        self,
        token: str,
        api_id: int,
        api_hash: str,
        session_dir: Path | str = "~/.tgkit/sessions",
    ):
        if not token or ":" not in token:
            raise ValueError(f"Invalid bot token: {token!r}")

        self.token = token
        self.bot_id = int(token.split(":")[0])
        self._session_dir = Path(session_dir).expanduser()
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self.session_name = str(self._session_dir / f"bot_{self.bot_id}")

        self.raw = Client(
            self.session_name,
            api_id=api_id,
            api_hash=api_hash,
            bot_token=token,
            workdir=str(self._session_dir),
            no_updates=False,          # MUST be False — update handler processes server_salt updates
            parse_mode=ParseMode.HTML,  # default to HTML for captions
            sleep_threshold=60,        # auto-handle FloodWaits up to 60s
        )

        self.peer = PeerResolver(self.raw)
        self._started = False
        self._start_lock = asyncio.Lock()
        self._username: str | None = None
        self._first_name: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the client and cache bot info."""
        async with self._start_lock:
            if self._started:
                return
            await self.raw.start()
            self._started = True
            me = await self.raw.get_me()
            self._username = me.username
            self._first_name = me.first_name
            logger.debug(f"TgClient started: bot_id={self.bot_id} @{self._username}")

    async def stop(self) -> None:
        """Stop the client."""
        if self._started:
            try:
                await self.raw.stop()
            except Exception as e:
                logger.debug(f"Error stopping TgClient {self.bot_id}: {e}")
            finally:
                self._started = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def username(self) -> str | None:
        return self._username

    @property
    def first_name(self) -> str | None:
        return self._first_name

    # ------------------------------------------------------------------
    # Peer resolution
    # ------------------------------------------------------------------

    async def resolve_peer(self, channel_id: int) -> bool:
        """Resolve a channel peer (cache access_hash).

        Required for channels with IDs > 2147483647 (int32 limit).
        Must be called BEFORE get_messages / send_document / forward_messages
        on such channels.

        Returns:
            True if resolved (or already cached).
        """
        return await self.peer.resolve(channel_id)

    async def prime_destination(self, channel_id: int) -> bool:
        """Prime a destination channel by sending + deleting a placeholder.

        This is needed because:
        - Bots can't use get_dialogs() (BotMethodInvalid)
        - The only way to cache a peer is to interact with it
        - send_message + delete_messages is the cheapest interaction

        For channels that the bot is an admin in, this works perfectly.
        For channels the bot can read but not write to, this will fail —
        in that case, the peer must be resolved via channels.GetChannels first.

        Returns:
            True if priming succeeded, False otherwise.
        """
        try:
            test_msg = await self.raw.send_message(
                channel_id, "_init_", disable_notification=True
            )
            await self.raw.delete_messages(channel_id, test_msg.id)
            return True
        except Exception as e:
            logger.debug(
                f"prime_destination({channel_id}) failed: {type(e).__name__}: {e}"
            )
            return False

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "TgClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        return f"TgClient(bot_id={self.bot_id}, @{self._username or '?'}, {status})"
