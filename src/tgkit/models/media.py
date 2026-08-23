"""Media model — file/media info extracted from a message."""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any


@dataclass
class MediaInfo:
    """Normalized media info from a Telegram message.

    Covers all media types: document, video, photo, audio, animation,
    voice, video_note. Fields not applicable to a type are None/0.
    """
    media_type: str = "none"       # document|video|photo|audio|animation|voice|video_note|text|none|other
    file_id: str = ""              # ⚠ ephemeral — per-bot, can rotate. Re-fetch at copy time.
    file_name: str = ""
    file_extension: str = ""
    file_size: int = 0
    mime_type: str = ""
    duration: int = 0              # seconds (video/audio/animation/voice/video_note)
    width: int = 0                 # pixels (video/photo/animation/video_note)
    height: int = 0                # pixels (video/photo/animation/video_note)
    has_thumb: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MediaInfo":
        return cls(
            media_type=str(d.get("media_type", "none")),
            file_id=str(d.get("file_id", "")),
            file_name=str(d.get("file_name", "")),
            file_extension=str(d.get("file_extension", "")),
            file_size=int(d.get("file_size", 0) or 0),
            mime_type=str(d.get("mime_type", "")),
            duration=int(d.get("duration", 0) or 0),
            width=int(d.get("width", 0) or 0),
            height=int(d.get("height", 0) or 0),
            has_thumb=bool(d.get("has_thumb", False)),
        )

    @property
    def file_size_mb(self) -> float:
        """File size in MB (rounded to 2 decimal places)."""
        if self.file_size <= 0:
            return 0.0
        return round(self.file_size / (1024 * 1024), 2)

    @property
    def file_size_gb(self) -> float:
        """File size in GB (rounded to 2 decimal places)."""
        if self.file_size <= 0:
            return 0.0
        return round(self.file_size / (1024 * 1024 * 1024), 2)

    def __repr__(self) -> str:
        return f"MediaInfo({self.media_type}, {self.file_name!r}, {self.file_size_mb} MB)"
