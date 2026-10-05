"""Telegram file-size limits — the single source of truth.

Why this module exists
----------------------
Telegram has TWO APIs with DIFFERENT size limits, and this is the #1 source
of confusion for both humans and AI agents:

    ┌──────────────────┬──────────────┬──────────────┐
    │                  │  download    │   upload     │
    ├──────────────────┼──────────────┼──────────────┤
    │ Bot API (HTTP)   │   20 MB      │   50 MB      │
    │ MTProto (pyrofork)│  2 GB       │   2 GB       │
    │ vault (chunked)  │  ∞ (2 GB per chunk-message)│
    └──────────────────┴──────────────┴──────────────┘

A bot that happily downloads ten 5 MB files will hit a wall on file #11 if
it is 122 MB — the Bot API answers `400 file is too big` and gives no hint
about what to do instead. Every tgkit command therefore consults this
module BEFORE doing anything, and either:
    1. automatically routes through MTProto (the capable transport), or
    2. refuses with a plain-language message naming the command to use.

All helpers are pure functions — trivially unit-testable, no I/O.
"""

from __future__ import annotations

# ── Hard limits (bytes) ──────────────────────────────────────────────
# Bot API (api.telegram.org) caps — enforced server-side by Telegram.
BOT_API_MAX_DOWNLOAD = 20 * 1024 * 1024          # 20 MB
BOT_API_MAX_UPLOAD = 50 * 1024 * 1024            # 50 MB

# MTProto caps (pyrofork / tgkit Tier 2).
MTPROTO_MAX = 2 * 1024 * 1024 * 1024             # 2 GB (per file / per chunk)

# Default vault chunk size (must stay in sync with config.vault.chunk_size_mb).
VAULT_CHUNK_MB = 19


def human_size(n: int | float | None) -> str:
    """Format a byte count for humans: 1536 → '1.5 KB'."""
    n = n or 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"  # unreachable, keeps type-checkers happy


def download_route(size_bytes: int | None) -> str:
    """Pick the download transport for a given size.

    Returns one of:
        "bot_api"  — small file, the Bot API (HTTP) can handle it
        "mtproto"  — over 20 MB: Bot API impossible, MTProto required
        "vault"    — a vault manifest (any total size, chunked)
        "impossible" — over 2 GB: NO bot can download this in one message
    """
    size_bytes = size_bytes or 0
    if size_bytes > MTPROTO_MAX:
        return "impossible"
    if size_bytes > BOT_API_MAX_DOWNLOAD:
        return "mtproto"
    return "bot_api"


def upload_route(size_bytes: int | None) -> str:
    """Pick the upload transport for a given size.

    Returns one of:
        "bot_api"  — under 50 MB: Bot API sendDocument works
        "mtproto"  — 50 MB..2 GB: must upload via MTProto
        "vault"    — over 2 GB: only the chunked vault can do it
    """
    size_bytes = size_bytes or 0
    if size_bytes > MTPROTO_MAX:
        return "vault"
    if size_bytes > BOT_API_MAX_UPLOAD:
        return "mtproto"
    return "bot_api"


def download_note(size_bytes: int | None) -> str | None:
    """Console note for downloads. None when the file is unremarkable."""
    size_bytes = size_bytes or 0
    if size_bytes > MTPROTO_MAX:
        return (
            f"{human_size(size_bytes)} — larger than Telegram's 2 GB ceiling. "
            f"A single message cannot hold this file; nothing can download it as-is."
        )
    if size_bytes > BOT_API_MAX_DOWNLOAD:
        return (
            f"{human_size(size_bytes)} — over the Bot API's 20 MB download cap "
            f"(plain `getFile` would fail with 'file is too big'), "
            f"so this download automatically runs through MTProto (works up to 2 GB)."
        )
    return None


def upload_note(size_bytes: int | None) -> str | None:
    """Console note for uploads. None when the file is unremarkable."""
    size_bytes = size_bytes or 0
    if size_bytes > MTPROTO_MAX:
        return (
            f"{human_size(size_bytes)} — over Telegram's 2 GB single-file ceiling. "
            f"Only `tgkit vault upload` can store this: it splits the file into "
            f"{VAULT_CHUNK_MB} MB chunks (no total-size limit)."
        )
    if size_bytes > BOT_API_MAX_UPLOAD:
        return (
            f"{human_size(size_bytes)} — over the Bot API's 50 MB upload cap, "
            f"so this upload automatically runs through MTProto (works up to 2 GB)."
        )
    return None
