"""Message models — MessageRef and MessageInfo.

MessageRef: a (channel, msg_id) pair — the minimal address of a message.
MessageInfo: the full normalized scan row (18 columns) — what a scan produces.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any

from tgkit.models.media import MediaInfo


@dataclass(frozen=True)
class MessageRef:
    """A minimal message address: (channel_id, msg_id).

    Frozen (hashable) so it can be used as dict key / set member.
    """
    channel_id: int | str   # int (numeric ID) or str ("@username")
    msg_id: int

    def to_dict(self) -> dict[str, Any]:
        return {"channel_id": self.channel_id, "msg_id": self.msg_id}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MessageRef":
        return cls(channel_id=d["channel_id"], msg_id=int(d["msg_id"]))

    def __repr__(self) -> str:
        return f"MessageRef({self.channel_id}, #{self.msg_id})"


@dataclass
class MessageInfo:
    """Full normalized scan row — 18 columns.

    This is what BatchedScanner produces per message, what gets written to the
    `messages` DB table, and what gets exported to CSV.
    """
    ref: MessageRef
    date: str = ""                 # ISO 8601 timestamp
    caption: str = ""
    caption_length: int = 0
    is_marker: bool = False        # True if caption is a series-boundary emoji
    marker_emoji: str = ""
    message_link: str = ""
    media: MediaInfo = field(default_factory=MediaInfo)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Flatten to 18-column dict (for CSV / DB)."""
        return {
            "msg_id": self.ref.msg_id,
            "message_link": self.message_link,
            "date": self.date,
            "media_type": self.media.media_type,
            "file_name": self.media.file_name,
            "file_extension": self.media.file_extension,
            "file_size": self.media.file_size,
            "file_size_mb": self.media.file_size_mb,
            "mime_type": self.media.mime_type,
            "duration": self.media.duration,
            "width": self.media.width,
            "height": self.media.height,
            "caption": self.caption,
            "caption_length": self.caption_length,
            "is_marker": self.is_marker,
            "marker_emoji": self.marker_emoji,
            "has_thumb": self.media.has_thumb,
            "file_id": self.media.file_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MessageInfo":
        """Reconstruct from 18-column dict (from CSV / DB)."""
        ref = MessageRef(
            channel_id=d.get("channel_id", 0),
            msg_id=int(d["msg_id"]),
        )
        media = MediaInfo(
            media_type=str(d.get("media_type", "none")),
            file_name=str(d.get("file_name", "")),
            file_extension=str(d.get("file_extension", "")),
            file_size=int(d.get("file_size", 0) or 0),
            mime_type=str(d.get("mime_type", "")),
            duration=int(d.get("duration", 0) or 0),
            width=int(d.get("width", 0) or 0),
            height=int(d.get("height", 0) or 0),
            has_thumb=bool(d.get("has_thumb", False)),
            file_id=str(d.get("file_id", "")),
        )
        return cls(
            ref=ref,
            date=str(d.get("date", "")),
            caption=str(d.get("caption", "")),
            caption_length=int(d.get("caption_length", 0) or 0),
            is_marker=bool(d.get("is_marker", False)),
            marker_emoji=str(d.get("marker_emoji", "")),
            message_link=str(d.get("message_link", "")),
            media=media,
        )

    def __repr__(self) -> str:
        return f"MessageInfo({self.ref}, {self.media.media_type}, {self.media.file_name!r})"
