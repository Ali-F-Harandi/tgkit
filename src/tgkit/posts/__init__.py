"""Posts subsystem — build linked-list caption messages.

Generic "post builder" for sharing many links in a channel:
    1. Main post (optionally with a photo/cover)
    2. If links don't fit in one caption, continuation messages as replies
    3. Each continuation links to the next via "→ continue" link

WORKS FOR ANY USE CASE:
    - Manga/comic chapter links
    - File collection links
    - Episode links
    - Any set of links that needs to span multiple messages

CAPTION LIMITS (Telegram):
    - Media caption: 1024 chars
    - Text message: 4096 chars
    - We use TEXT messages for link lists (more room) and optionally
      a media message as the "cover" / first post.
"""

from __future__ import annotations

from tgkit.posts.builder import (
    PostBuilder, PostConfig, PostResult, LinkItem,
    build_caption_parts, split_links_into_parts,
)

__all__ = [
    "PostBuilder",
    "PostConfig",
    "PostResult",
    "LinkItem",
    "build_caption_parts",
    "split_links_into_parts",
]
