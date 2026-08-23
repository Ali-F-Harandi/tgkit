"""DB sync — upload/download/discover the SQLite DB file in a Telegram channel.

The DB is the central state of tgkit. It lives as a .db file in a designated
"DB sync channel". Every operation updates the local DB; after batch operations,
the local DB is synced to the channel.

KEY DESIGN: Atomic replace via editMessageMedia
    When updating the DB in the channel, we use editMessageMedia to replace
    the file in-place. This preserves the message_id, so all references to
    the DB (stored in config) stay valid. No delete+reupload race condition.

DB DISCOVERY:
    If the DB message_id is unknown or stale, we can discover it by scanning
    recent messages in the DB channel. This requires Tier 2 (MTProto) because
    Bot API has no getHistory.

    Discovery scans the last 200 messages looking for a document with caption
    starting with "TGKIT_DB_BACKUP" or filename matching "tgkit*.db".

SYNC FLOW:
    1. At start: download DB from channel (or discover it)
    2. Operations update local DB
    3. After batch: upload updated DB (editMessageMedia if msg_id known, else send + delete old)
    4. Store msg_id in config for next time
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from tgkit.config.schema import Config
from tgkit.db.connection import Database
from tgkit.transport.bot_api import BotAPIClient, BotAPIError

logger = logging.getLogger(__name__)

# Caption prefix for DB backup messages — used for discovery
DB_CAPTION_PREFIX = "TGKIT_DB_BACKUP"
DB_FILENAME_PATTERN = "tgkit"


class DBSync:
    """Sync the SQLite DB to/from a Telegram channel.

    Attributes:
        config: tgkit Config (for bot tokens, db_sync channel)
        db_path: local path to the SQLite DB file
        bot_api: BotAPIClient for HTTP operations (works in both tiers)
    """

    def __init__(self, config: Config, db_path: Path):
        self.config = config
        self.db_path = db_path
        self._bot_api: BotAPIClient | None = None

    @property
    def bot_api(self) -> BotAPIClient:
        """Lazy-init BotAPIClient (uses first bot token)."""
        if self._bot_api is None:
            if not self.config.bots:
                raise RuntimeError("No bots configured")
            self._bot_api = BotAPIClient(self.config.bots[0].token)
        return self._bot_api

    @property
    def sync_channel(self) -> int | None:
        """The DB sync channel ID (from config)."""
        return self.config.channels.db_sync

    @property
    def sync_msg_id(self) -> int | None:
        """The known DB message ID (from config)."""
        return self.config.db.sync_msg_id

    # ------------------------------------------------------------------
    # Upload (sync local DB → channel)
    # ------------------------------------------------------------------

    def upload(self, description: str = "") -> int | None:
        """Upload the local DB to the sync channel.

        If sync_msg_id is known: uses editMessageMedia to replace in-place
        (atomic, preserves message_id).

        If sync_msg_id is unknown: sends a new document, deletes the old one
        if it existed, and stores the new msg_id in config.

        Returns:
            The message_id of the DB in the channel, or None on failure.
        """
        if not self.sync_channel:
            logger.error("No DB sync channel configured")
            return None

        if not self.db_path.exists():
            logger.error(f"DB file not found: {self.db_path}")
            return None

        caption = self._build_caption(description)

        try:
            if self.sync_msg_id:
                # Atomic replace via editMessageMedia
                return self._replace_in_place(caption)
            else:
                # First upload or msg_id was lost
                return self._upload_new(caption)
        except BotAPIError as e:
            logger.error(f"DB upload failed: {e}")
            return None

    def _replace_in_place(self, caption: str) -> int:
        """Replace DB file in-place via editMessageMedia (preserves msg_id)."""
        try:
            self.bot_api.edit_message_media(
                chat_id=self.sync_channel,
                message_id=self.sync_msg_id,
                media_path=str(self.db_path),
                media_type="document",
            )
            # Also update the caption
            try:
                self.bot_api.edit_message_caption(
                    chat_id=self.sync_channel,
                    message_id=self.sync_msg_id,
                    caption=caption,
                )
            except BotAPIError:
                pass  # caption edit is non-critical
            logger.info(f"DB updated in-place (msg_id={self.sync_msg_id})")
            return self.sync_msg_id
        except BotAPIError as e:
            logger.warning(f"editMessageMedia failed ({e}), falling back to new upload")
            return self._upload_new(caption)

    def _upload_new(self, caption: str) -> int:
        """Upload DB as a new message. Deletes old one if known."""
        result = self.bot_api.send_document(
            chat_id=self.sync_channel,
            document_path=str(self.db_path),
            caption=caption,
        )
        new_msg_id = result.get("message_id", 0)

        # Delete old DB message if we had one
        if self.sync_msg_id and self.sync_msg_id != new_msg_id:
            try:
                self.bot_api.delete_message(self.sync_channel, self.sync_msg_id)
                logger.debug(f"Deleted old DB message (msg_id={self.sync_msg_id})")
            except BotAPIError:
                pass  # non-critical

        # Update config with new msg_id
        self.config.db.sync_msg_id = new_msg_id
        self._save_config()

        logger.info(f"DB uploaded as new message (msg_id={new_msg_id})")
        return new_msg_id

    # ------------------------------------------------------------------
    # Download (sync channel → local DB)
    # ------------------------------------------------------------------

    def download(self) -> bool:
        """Download the DB from the sync channel.

        If sync_msg_id is known: download directly.
        If unknown: attempt discovery first (requires Tier 2).

        Returns:
            True if download succeeded, False otherwise.
        """
        if not self.sync_channel:
            logger.error("No DB sync channel configured")
            return False

        msg_id = self.sync_msg_id
        if not msg_id:
            # Try to discover
            msg_id = self.discover()
            if not msg_id:
                logger.info("No DB found in channel (first run?)")
                return False

        try:
            return self._download_by_msg_id(msg_id)
        except BotAPIError as e:
            logger.error(f"DB download failed: {e}")
            return False

    def _download_by_msg_id(self, msg_id: int) -> bool:
        """Download DB file by message ID via Bot API forwardMessage trick.

        Bot API can't directly read a channel message by ID. We use forwardMessage
        to forward it to the same channel (if bot is admin), read the result,
        then delete the forward. This works because forwardMessage returns
        the full message including document.

        Alternative: if we have MTProto (Tier 2), use get_messages directly.
        """
        # Try forwardMessage approach (works in both tiers)
        try:
            # Forward to same channel (bot must be admin)
            forwarded = self.bot_api.forward_message(
                chat_id=self.sync_channel,
                from_chat_id=self.sync_channel,
                message_id=msg_id,
                disable_notification=True,
            )

            # Extract file_id from forwarded message
            document = forwarded.get("document", {})
            file_id = document.get("file_id")
            file_size = document.get("file_size", 0)

            if not file_id:
                logger.error("Forwarded message has no document")
                self.bot_api.delete_message(self.sync_channel, forwarded["message_id"])
                return False

            # Download via getFile + HTTP (works for files < 20 MB)
            # DB files are typically < 20 MB, so this is fine
            temp_path = str(self.db_path) + ".tmp"
            self.bot_api.download_file(file_id, temp_path)

            # Delete the forward
            self.bot_api.delete_message(self.sync_channel, forwarded["message_id"])

            # Atomic rename
            os.replace(temp_path, self.db_path)

            # Update config with msg_id
            self.config.db.sync_msg_id = msg_id
            self._save_config()

            logger.info(f"DB downloaded (msg_id={msg_id}, {file_size} bytes)")
            return True

        except BotAPIError as e:
            logger.error(f"DB download via forwardMessage failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Discovery (find DB in channel by scanning recent messages)
    # ------------------------------------------------------------------

    def discover(self) -> int | None:
        """Find the DB message in the sync channel.

        Strategy: Try recent message IDs backwards (msg_id-1, msg_id-2, ...)
        using forwardMessage to inspect each. Stop at first match.

        This is expensive (one forwardMessage per ID checked) but reliable.
        Only used when sync_msg_id is unknown or stale.

        For Tier 2 (MTProto), a faster approach uses get_messages with
        batch IDs — implemented in discover_mtproto().

        Returns:
            The message_id of the DB, or None if not found.
        """
        if not self.sync_channel:
            return None

        # First, try the stored msg_id (might still work)
        if self.sync_msg_id:
            if self._is_db_message(self.sync_msg_id):
                logger.info(f"DB found at stored msg_id={self.sync_msg_id}")
                return self.sync_msg_id

        # Scan backwards from recent message IDs
        # We don't know the latest msg_id, so we send a marker to get it
        try:
            marker = self.bot_api.send_message(
                self.sync_channel, "TGKIT_DB_SCAN_MARKER",
                disable_notification=True,
            )
            latest_msg_id = marker["message_id"]
            self.bot_api.delete_message(self.sync_channel, latest_msg_id)
        except BotAPIError as e:
            logger.error(f"Cannot determine latest msg_id: {e}")
            return None

        # Scan backwards (up to 200 messages)
        logger.info(f"Scanning for DB in last 200 messages (from msg_id={latest_msg_id})...")
        for offset in range(1, 201):
            check_id = latest_msg_id - offset
            if check_id <= 0:
                break

            if self._is_db_message(check_id):
                logger.info(f"DB discovered at msg_id={check_id}")
                self.config.db.sync_msg_id = check_id
                self._save_config()
                return check_id

        logger.info("DB not found in last 200 messages")
        return None

    def _is_db_message(self, msg_id: int) -> bool:
        """Check if a message is the DB backup.

        Uses forwardMessage to inspect the message (Bot API can't read by ID).
        Deletes the forward immediately after checking.
        """
        try:
            forwarded = self.bot_api.forward_message(
                chat_id=self.sync_channel,
                from_chat_id=self.sync_channel,
                message_id=msg_id,
                disable_notification=True,
            )

            # Check if it's a document with DB-like filename/caption
            is_db = False
            document = forwarded.get("document", {})
            caption = forwarded.get("caption", "")
            filename = ""
            if document:
                filename = document.get("file_name", "")

            if caption.startswith(DB_CAPTION_PREFIX):
                is_db = True
            elif DB_FILENAME_PATTERN in filename.lower() and filename.endswith(".db"):
                is_db = True

            # Clean up the forward
            self.bot_api.delete_message(self.sync_channel, forwarded["message_id"])

            return is_db

        except BotAPIError:
            return False

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Get DB sync status."""
        local_exists = self.db_path.exists()
        local_size = self.db_path.stat().st_size if local_exists else 0
        local_modified = (
            self.db_path.stat().st_mtime if local_exists else None
        )

        return {
            "sync_channel": self.sync_channel,
            "sync_msg_id": self.sync_msg_id,
            "local_path": str(self.db_path),
            "local_exists": local_exists,
            "local_size": local_size,
            "local_modified": local_modified,
            "auto_sync": self.config.db.auto_sync,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_caption(self, description: str = "") -> str:
        """Build the DB backup caption."""
        from datetime import datetime
        size = self.db_path.stat().st_size if self.db_path.exists() else 0
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        lines = [
            f"{DB_CAPTION_PREFIX}",
            f"Size: {size:,} bytes ({size/1024:.1f} KB)",
            f"Synced: {timestamp}",
        ]
        if description:
            lines.append(f"Note: {description}")
        return "\n".join(lines)

    def _save_config(self) -> None:
        """Save config to disk."""
        from tgkit.config.loader import save_config
        from tgkit.config.loader import find_config_path
        config_path = find_config_path()
        save_config(self.config, config_path)

    def close(self) -> None:
        """Close the Bot API client."""
        if self._bot_api:
            self._bot_api.close()
