"""Channel model — represents a Telegram channel/supergroup/chat."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class Channel:
    """A Telegram channel/supergroup/chat.

    Attributes:
        id: Full channel ID (e.g., -1003873843444) or username (e.g., "@yxafile")
            Stored as int if numeric, str if username.
        username: Public username (without @), or None if private
        access_hash: Cached MTProto access_hash (for 64-bit ID peer resolution)
        title: Channel title (filled after resolution)
        type: 'public' | 'private' | 'supergroup' | 'chat'
        role: How this channel is used in tgkit:
            'source'           — read-only source (e.g., @yxafile)
            'destination'      — where copy/forward results land
            'vault_main'       — primary vault storage (chunked uploads)
            'vault_temp'       — temp channel for Bot API forwards
            'vault_storage'    — additional vault storage channels
            'sync'             — DB sync backup channel
        last_scanned_at: Unix timestamp of last scan (None if never scanned)
        last_message_id: Highest known message ID (for incremental scans)
    """
    id: int | str
    username: str | None = None
    access_hash: int | None = None
    title: str | None = None
    type: str | None = None
    role: str = "source"
    last_scanned_at: float | None = None
    last_message_id: int | None = None

    @property
    def is_numeric_id(self) -> bool:
        """True if id is a numeric channel ID (not a username)."""
        return isinstance(self.id, int)

    @property
    def internal_id(self) -> int | None:
        """Internal channel ID (strip -100 prefix from numeric IDs).

        -1003873843444 → 3873843444
        Returns None for username-based channels.
        """
        if not self.is_numeric_id:
            return None
        id_str = str(self.id)
        if id_str.startswith("-100"):
            return int(id_str[4:])
        return abs(self.id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Channel":
        return cls(
            id=d["id"] if isinstance(d["id"], (int, str)) else int(d["id"]),
            username=d.get("username"),
            access_hash=d.get("access_hash"),
            title=d.get("title"),
            type=d.get("type"),
            role=d.get("role", "source"),
            last_scanned_at=d.get("last_scanned_at"),
            last_message_id=d.get("last_message_id"),
        )

    def __repr__(self) -> str:
        if self.username:
            return f"Channel(@{self.username}, role={self.role!r})"
        return f"Channel({self.id}, role={self.role!r})"
