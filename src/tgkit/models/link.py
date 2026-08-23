"""Link parsing — convert between t.me URLs and (channel, msg_id) pairs.

Supports:
    https://t.me/<username>/<msg_id>           → ("@<username>", msg_id)
    https://t.me/c/<internal_id>/<msg_id>      → (-100<internal_id>, msg_id)
    https://telegram.me/<username>/<msg_id>    → ("@<username>", msg_id)
    @<username>/<msg_id>                       → ("@<username>", msg_id)
    -100<internal_id>/<msg_id>                 → (-100<internal_id>, msg_id)
"""

from __future__ import annotations

import re
from typing import Any

# Regex for t.me links (handles telegram.me and t.me)
_LINK_RE = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me)/(?:c/)?(?P<ident>[^/]+)/(?P<msg_id>\d+)"
)


def parse_link(link: str) -> tuple[int | str, int]:
    """Parse a t.me link → (from_chat_id, message_id).

    Args:
        link: A t.me URL or @username/msg_id or -100xxx/msg_id

    Returns:
        Tuple of (channel_id, msg_id) where channel_id is:
            - "@username" (str) for public channels
            - -100<internal_id> (int) for private channels

    Raises:
        ValueError: if the link cannot be parsed
    """
    link = link.strip()
    m = _LINK_RE.match(link)
    if not m:
        # Try @username/123 format
        if "/" in link and link.startswith("@"):
            username, _, msg_id = link.partition("/")
            try:
                return username, int(msg_id)
            except ValueError:
                pass
        # Try -100xxx/123 format
        if "/" in link and link.startswith("-100"):
            id_str, _, msg_id = link.partition("/")
            try:
                return int(id_str), int(msg_id)
            except ValueError:
                pass
        raise ValueError(f"Cannot parse link: {link!r}")

    ident = m.group("ident")
    msg_id = int(m.group("msg_id"))

    if ident.isdigit():
        # Private channel: t.me/c/<internal_id>/<msg_id>
        return int(f"-100{ident}"), msg_id
    else:
        # Public channel: t.me/<username>/<msg_id>
        return f"@{ident}", msg_id


def build_link(channel_id: int | str, msg_id: int) -> str:
    """Build a t.me link from (channel_id, msg_id).

    For public channels (username), produces: https://t.me/<username>/<msg_id>
    For private channels (numeric ID), produces: https://t.me/c/<internal_id>/<msg_id>

    Args:
        channel_id: "@username" (str) or -100<internal_id> (int)
        msg_id: Message ID

    Returns:
        A t.me URL
    """
    if isinstance(channel_id, str):
        # Username form — strip @ if present
        username = channel_id.lstrip("@")
        return f"https://t.me/{username}/{msg_id}"
    else:
        # Numeric ID — extract internal ID
        id_str = str(channel_id)
        if id_str.startswith("-100"):
            internal = id_str[4:]
        else:
            internal = str(abs(channel_id))
        return f"https://t.me/c/{internal}/{msg_id}"


def parse_channel_ref(ref: str) -> int | str:
    """Parse a channel reference into a normalized form.

    Accepts:
        "@username" or "username" → "@username"
        "-1003873843444" → -1003873843444 (int)
        "3873843444" → -1003873843444 (int, prefixed with -100)

    Returns:
        int (numeric ID) or str ("@username")
    """
    ref = ref.strip()

    # Username form
    if ref.startswith("@"):
        return ref
    if not ref.lstrip("-").isdigit():
        # Treat as username
        return f"@{ref.lstrip('@')}"

    # Numeric form
    num = int(ref)
    if num < 0:
        # Already has -100 prefix
        return num
    # Bare positive number — assume internal ID, add -100 prefix
    return int(f"-100{num}")
