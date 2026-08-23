"""Smart gzip compression — skip already-compressed formats.

Ported from tg-vault/compression.py — proven skip-list.
"""

from __future__ import annotations

import gzip
import io
from pathlib import Path

# File extensions that are already compressed — skip gzip
SKIP_COMPRESSION_EXTENSIONS = frozenset({
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".ico", ".heic",
    # Video
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    # Audio
    ".mp3", ".aac", ".ogg", ".opus", ".flac", ".m4a", ".wma",
    # Archives
    ".zip", ".rar", ".7z", ".gz", ".bz2", ".xz", ".tar", ".tgz", ".tbz2",
    # Comic formats (already zipped)
    ".cbz", ".cbr", ".cb7", ".cbt",
    # Already-compressed docs
    ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
    # Disk images
    ".dmg", ".iso", ".apk", ".ipa", ".deb", ".rpm",
    # Encrypted blobs
    ".enc", ".gpg", ".pgp", ".age",
})


def should_skip_compression(filename: str) -> bool:
    """Check if a file should skip compression based on its extension."""
    if not filename:
        return True

    # Handle compound extensions (.tar.gz, .tar.bz2, .tar.xz)
    lower = filename.lower()
    if lower.endswith(".tar.gz") or lower.endswith(".tar.bz2") or lower.endswith(".tar.xz"):
        return True

    ext = Path(lower).suffix
    return ext in SKIP_COMPRESSION_EXTENSIONS


def compress_data(data: bytes, filename: str = "", level: int = 6) -> tuple[bytes, bool]:
    """Compress data with gzip if beneficial.

    Args:
        data: Raw data to compress
        filename: Used to skip already-compressed formats
        level: gzip compression level (1-9, default 6)

    Returns:
        Tuple of (data, was_compressed):
            - If compressed: (compressed_data, True)
            - If skipped (already compressed or compression didn't help): (original_data, False)
    """
    if should_skip_compression(filename):
        return data, False

    try:
        compressed = gzip.compress(data, compresslevel=level)
        # Only use compressed version if it's actually smaller
        if len(compressed) < len(data):
            return compressed, True
        return data, False
    except Exception:
        return data, False


def decompress_data(data: bytes, was_compressed: bool) -> bytes:
    """Decompress data if it was compressed.

    Args:
        data: Data to decompress
        was_compressed: Whether the data was compressed (from compress_data or chunk header)

    Returns:
        Original (decompressed) data
    """
    if not was_compressed:
        return data

    try:
        return gzip.decompress(data)
    except Exception:
        # Fallback: if gzip fails, return raw data (older versions had a bug
        # where compressed=True even when gzip didn't help)
        return data
