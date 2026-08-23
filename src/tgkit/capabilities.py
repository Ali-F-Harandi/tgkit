"""Capability detection — Tier 1 (Bot API) vs Tier 2 (MTProto).

Tier 1 — Basic (1 bot token, NO api_id/api_hash):
    ✓ Send messages/files to channels where bot is admin
    ✓ Forward messages (WITH "Forwarded from" header)
    ✓ copyMessage (returns only new msg_id, no metadata)
    ✓ Edit caption/text, delete messages
    ✓ Reply to messages
    ✗ Cannot read channel history (no getHistory/getMessage)
    ✗ Cannot copy via file_id (no header removal)
    ✗ Cannot scan channels
    ✗ Upload limit: 50 MB, Download limit: 20 MB

Tier 2 — Extended (bot token + api_id + api_hash via pyrofork/MTProto):
    Everything in Tier 1, plus:
    ✓ Read channel history: get_messages(channel, [up to 200 IDs])
    ✓ Copy via file_id: send_document(file_id) — NO forward header, fully editable
    ✓ Download/upload up to 2 GB
    ✓ Full metadata extraction (filename, size, mime, dimensions, duration)
    ✓ Scan channels (backwards from any message ID)
    ✓ DB discovery (scan channel for DB file by caption prefix)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Flag
from typing import Any

from tgkit.config.schema import Config


class Capability(Flag):
    """Bitmask of tgkit capabilities."""
    NONE = 0
    # Tier 1 — Bot API
    SEND_MESSAGE = 1
    SEND_FILE = 2
    FORWARD = 4              # with header
    COPY_MESSAGE = 8         # copyMessage (returns only msg_id)
    EDIT_CAPTION = 16
    EDIT_TEXT = 32
    EDIT_MEDIA = 64           # editMessageMedia (replace file in-place)
    DELETE = 128
    REPLY = 256
    DB_SYNC = 512              # upload/download DB file by msg_id

    # Tier 2 — MTProto
    READ_HISTORY = 1024         # get_messages(channel, [ids])
    COPY_FILE_ID = 2048         # send_document(file_id) — no header
    DOWNLOAD_LARGE = 4096       # > 20 MB
    UPLOAD_LARGE = 8192         # > 50 MB
    SCAN_CHANNEL = 16384         # backwards scan from msg_id
    DB_DISCOVERY = 32768         # find DB in channel by scanning
    FULL_METADATA = 65536        # extract filename/size/mime/dims from messages

    # Convenience groups
    TIER1_BASIC = SEND_MESSAGE | SEND_FILE | FORWARD | COPY_MESSAGE | EDIT_CAPTION | EDIT_TEXT | EDIT_MEDIA | DELETE | REPLY | DB_SYNC
    TIER2_EXTENDED = READ_HISTORY | COPY_FILE_ID | DOWNLOAD_LARGE | UPLOAD_LARGE | SCAN_CHANNEL | DB_DISCOVERY | FULL_METADATA
    FULL = TIER1_BASIC | TIER2_EXTENDED


@dataclass
class CapabilityReport:
    """Snapshot of current capabilities based on config."""
    tier: str                   # "tier1_basic" | "tier2_extended" | "none"
    capabilities: Capability
    bot_count: int
    has_api_credentials: bool
    has_destination_channel: bool
    has_db_sync_channel: bool
    missing_for_tier2: list[str]

    def can(self, cap: Capability) -> bool:
        """Check if a specific capability is available."""
        return bool(self.capabilities & cap)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "bot_count": self.bot_count,
            "has_api_credentials": self.has_api_credentials,
            "has_destination_channel": self.has_destination_channel,
            "has_db_sync_channel": self.has_db_sync_channel,
            "missing_for_tier2": self.missing_for_tier2,
            "capabilities": [c.name for c in Capability if c != Capability.NONE and self.can(c)],
        }


def detect_capabilities(config: Config) -> CapabilityReport:
    """Detect available capabilities based on config.

    Returns a CapabilityReport describing what the tool can do right now.
    """
    caps = Capability.NONE
    missing = []

    has_bots = len(config.bots) > 0
    has_api = bool(config.api.api_id and config.api.api_hash)

    # Tier 1 — needs at least 1 bot
    if has_bots:
        caps |= Capability.TIER1_BASIC

    # Tier 2 — needs bots + api_id + api_hash
    if has_bots and has_api:
        caps |= Capability.TIER2_EXTENDED
    elif has_bots and not has_api:
        missing.append("api_id + api_hash (from https://my.telegram.org)")

    # Determine tier
    if caps & Capability.TIER2_EXTENDED:
        tier = "tier2_extended"
    elif caps & Capability.TIER1_BASIC:
        tier = "tier1_basic"
    else:
        tier = "none"
        missing.append("at least 1 bot token (tgkit bot add <TOKEN>)")

    has_dest = config.channels.default_destination is not None
    has_db_sync = config.channels.db_sync is not None

    return CapabilityReport(
        tier=tier,
        capabilities=caps,
        bot_count=len(config.bots),
        has_api_credentials=has_api,
        has_destination_channel=has_dest,
        has_db_sync_channel=has_db_sync,
        missing_for_tier2=missing,
    )


# ============================================================================
# Capability descriptions (for display)
# ============================================================================

CAPABILITY_DESCRIPTIONS: dict[Capability, str] = {
    Capability.SEND_MESSAGE:    "Send text messages to channels",
    Capability.SEND_FILE:       "Upload files (up to 50 MB)",
    Capability.FORWARD:         "Forward messages (with 'Forwarded from' header)",
    Capability.COPY_MESSAGE:    "Copy messages via copyMessage (header removed, no metadata)",
    Capability.EDIT_CAPTION:    "Edit message captions",
    Capability.EDIT_TEXT:       "Edit text messages",
    Capability.EDIT_MEDIA:      "Replace media in-place (editMessageMedia)",
    Capability.DELETE:          "Delete messages",
    Capability.REPLY:           "Send messages as replies",
    Capability.DB_SYNC:         "Sync database file to channel by msg_id",
    Capability.READ_HISTORY:    "Read channel history by message ID (get_messages)",
    Capability.COPY_FILE_ID:    "Copy via file_id — no header, fully editable, instant",
    Capability.DOWNLOAD_LARGE:  "Download files > 20 MB (up to 2 GB)",
    Capability.UPLOAD_LARGE:    "Upload files > 50 MB (up to 2 GB)",
    Capability.SCAN_CHANNEL:    "Scan channel history backwards from any message",
    Capability.DB_DISCOVERY:    "Find DB file in channel by scanning recent messages",
    Capability.FULL_METADATA:   "Extract full metadata (filename, size, mime, dimensions)",
}
