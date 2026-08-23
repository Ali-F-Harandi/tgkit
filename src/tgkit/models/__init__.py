"""Models: transport-agnostic data abstractions."""

from __future__ import annotations

from tgkit.models.channel import Channel
from tgkit.models.message import MessageRef, MessageInfo
from tgkit.models.media import MediaInfo
from tgkit.models.link import parse_link, build_link, parse_channel_ref

__all__ = [
    "Channel",
    "MessageRef",
    "MessageInfo",
    "MediaInfo",
    "parse_link",
    "build_link",
    "parse_channel_ref",
]
