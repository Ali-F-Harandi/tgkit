"""Shared utilities for tgkit."""

from __future__ import annotations

import hashlib


def stable_channel_db_id(channel: str | int) -> int:
    """Compute a stable, deterministic channel_db_id from a channel ref.

    Python's built-in ``hash()`` is randomized per-process (PYTHONHASHSEED),
    so it can't be used for DB keys that need to be stable across runs.
    We use the first 8 bytes of SHA256 as a signed 64-bit integer instead.

    Previously this bug existed in 9 call sites (commands/, operations/,
    vault/uploader) — channel records created in one run would never match
    lookups in the next run. All call sites now use this function.

    Args:
        channel: "@username" or numeric channel ID

    Returns:
        A stable 64-bit integer that uniquely identifies this channel.
        Numeric IDs are returned unchanged.
    """
    if isinstance(channel, int):
        return channel
    h = hashlib.sha256(str(channel).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big", signed=True)
