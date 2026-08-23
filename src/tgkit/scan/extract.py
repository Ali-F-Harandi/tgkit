"""Message extraction — convert pyrofork Message → normalized MessageInfo.

This module knows how to extract metadata from ALL media types:
    document, video, photo, audio, animation, voice, video_note, text

It does NOT download the file — only extracts the metadata that
get_messages() returns (filename, size, mime, dimensions, duration, file_id).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pyrogram.types import Message

from tgkit.models.message import MessageInfo, MessageRef
from tgkit.models.media import MediaInfo


def extract_channel_id(msg: Message) -> int | str:
    """Extract the channel ID from a message.

    Returns:
        int (numeric ID like -1001234567890) or str ("@username")
    """
    if msg.chat:
        # msg.chat.id is the negative form for channels (-100...)
        return msg.chat.id
    return 0


def extract_message_info(msg: Message, channel_username: str | None = None) -> MessageInfo:
    """Extract normalized MessageInfo from a pyrofork Message.

    Args:
        msg: pyrofork Message object
        channel_username: Override username (for building message_link)
                         If None, uses msg.chat.username

    Returns:
        MessageInfo with all 18 columns filled
    """
    # Determine channel ID and username for the link
    chat_id = msg.chat.id if msg.chat else 0
    username = channel_username or (msg.chat.username if msg.chat else None)

    # Build message link
    if username:
        message_link = f"https://t.me/{username}/{msg.id}"
    elif chat_id and str(chat_id).startswith("-100"):
        internal = str(chat_id)[4:]  # strip -100
        message_link = f"https://t.me/c/{internal}/{msg.id}"
    else:
        message_link = ""

    # Extract caption/text
    caption = msg.caption or msg.text or ""

    # Extract media info
    media = _extract_media(msg)

    return MessageInfo(
        ref=MessageRef(channel_id=chat_id, msg_id=msg.id),
        date=msg.date.isoformat() if msg.date else "",
        caption=caption,
        caption_length=len(caption),
        is_marker=False,       # generic — marker detection is use-case specific
        marker_emoji="",
        message_link=message_link,
        media=media,
    )


def _extract_media(msg: Message) -> MediaInfo:
    """Extract MediaInfo from a pyrofork Message.

    Handles all media types: document, video, photo, audio, animation,
    voice, video_note.
    """
    media = MediaInfo()

    if msg.document:
        media.media_type = "document"
        media.file_id = msg.document.file_id or ""
        media.file_name = msg.document.file_name or ""
        media.file_size = msg.document.file_size or 0
        media.mime_type = msg.document.mime_type or ""
        media.has_thumb = bool(msg.document.thumbs)
        if media.file_name:
            media.file_extension = Path(media.file_name).suffix.lower()

    elif msg.video:
        media.media_type = "video"
        media.file_id = msg.video.file_id or ""
        media.file_name = msg.video.file_name or ""
        media.file_size = msg.video.file_size or 0
        media.mime_type = msg.video.mime_type or ""
        media.duration = msg.video.duration or 0
        media.width = msg.video.width or 0
        media.height = msg.video.height or 0
        media.has_thumb = bool(msg.video.thumbs)
        if media.file_name:
            media.file_extension = Path(media.file_name).suffix.lower()

    elif msg.photo:
        media.media_type = "photo"
        media.file_id = msg.photo.file_id or ""
        media.file_size = msg.photo.file_size or 0
        media.width = msg.photo.width or 0
        media.height = msg.photo.height or 0
        media.has_thumb = bool(msg.photo.thumbs)

    elif msg.audio:
        media.media_type = "audio"
        media.file_id = msg.audio.file_id or ""
        media.file_name = msg.audio.file_name or ""
        media.file_size = msg.audio.file_size or 0
        media.mime_type = msg.audio.mime_type or ""
        media.duration = msg.audio.duration or 0
        media.has_thumb = bool(msg.audio.thumbs)
        if media.file_name:
            media.file_extension = Path(media.file_name).suffix.lower()

    elif msg.animation:
        media.media_type = "animation"
        media.file_id = msg.animation.file_id or ""
        media.file_name = msg.animation.file_name or ""
        media.file_size = msg.animation.file_size or 0
        media.mime_type = msg.animation.mime_type or ""
        media.duration = msg.animation.duration or 0
        media.width = msg.animation.width or 0
        media.height = msg.animation.height or 0
        media.has_thumb = bool(msg.animation.thumbs)

    elif msg.voice:
        media.media_type = "voice"
        media.file_id = msg.voice.file_id or ""
        media.file_size = msg.voice.file_size or 0
        media.duration = msg.voice.duration or 0

    elif msg.video_note:
        media.media_type = "video_note"
        media.file_id = msg.video_note.file_id or ""
        media.file_size = msg.video_note.file_size or 0
        media.duration = msg.video_note.duration or 0
        media.width = msg.video_note.length or 0
        media.height = msg.video_note.length or 0

    elif msg.text:
        media.media_type = "text"

    elif msg.media:
        media.media_type = "other"

    else:
        media.media_type = "none"

    return media
