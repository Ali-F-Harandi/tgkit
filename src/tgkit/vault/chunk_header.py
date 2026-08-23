"""TGV1 chunk header — 40-byte self-describing header prepended to each chunk.

Format (struct.pack("<4sHHIIQ16s", ...)):
    Offset 0:  "TGV1" magic (4 bytes)
    Offset 4:  version (uint16 LE) = 1
    Offset 6:  flags (uint16 LE) — bit 0 compressed, bit 1 encrypted
    Offset 8:  chunk_index (uint32 LE)
    Offset 12: total_chunks (uint32 LE)
    Offset 16: original_size (uint64 LE) — size of this chunk BEFORE compression/encryption
    Offset 24: sha256_prefix (16 bytes) — first 16 bytes of file SHA256

The header allows identifying chunks without the DB — if the manifest is lost,
chunks can still be reassembled by reading the headers.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

# Header constants
HEADER_MAGIC = b"TGV1"
HEADER_SIZE = 40
HEADER_FORMAT = "<4sHHIIQ16s"  # magic, version, flags, chunk_idx, total, orig_size, sha256_prefix

# Flags
FLAG_COMPRESSED = 1  # bit 0
FLAG_ENCRYPTED = 2   # bit 1


@dataclass
class ChunkHeader:
    """Parsed TGV1 header."""
    magic: bytes
    version: int
    flags: int
    chunk_index: int
    total_chunks: int
    original_size: int
    sha256_prefix: bytes

    @property
    def is_compressed(self) -> bool:
        return bool(self.flags & FLAG_COMPRESSED)

    @property
    def is_encrypted(self) -> bool:
        return bool(self.flags & FLAG_ENCRYPTED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "magic": self.magic.decode("ascii", errors="replace"),
            "version": self.version,
            "flags": self.flags,
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
            "original_size": self.original_size,
            "sha256_prefix": self.sha256_prefix.hex(),
            "is_compressed": self.is_compressed,
            "is_encrypted": self.is_encrypted,
        }


def create_header(
    chunk_index: int,
    total_chunks: int,
    original_size: int,
    sha256_prefix: bytes,
    flags: int = 0,
) -> bytes:
    """Create a 40-byte TGV1 header.

    Args:
        chunk_index: 0-based chunk index
        total_chunks: Total number of chunks
        original_size: Size of this chunk's original data (before compression/encryption)
        sha256_prefix: First 16 bytes of the file's SHA256
        flags: FLAG_COMPRESSED | FLAG_ENCRYPTED

    Returns:
        40 bytes
    """
    if len(sha256_prefix) < 16:
        sha256_prefix = sha256_prefix.ljust(16, b"\x00")
    elif len(sha256_prefix) > 16:
        sha256_prefix = sha256_prefix[:16]

    return struct.pack(
        HEADER_FORMAT,
        HEADER_MAGIC,
        1,  # version
        flags,
        chunk_index,
        total_chunks,
        original_size,
        sha256_prefix,
    )


def parse_header(data: bytes) -> ChunkHeader | None:
    """Parse a 40-byte TGV1 header. Returns None if data doesn't start with TGV1."""
    if len(data) < HEADER_SIZE:
        return None
    if data[:4] != HEADER_MAGIC:
        return None

    magic, version, flags, chunk_index, total_chunks, original_size, sha256_prefix = \
        struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])

    return ChunkHeader(
        magic=magic,
        version=version,
        flags=flags,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        original_size=original_size,
        sha256_prefix=sha256_prefix,
    )


def is_chunk_with_header(data: bytes) -> bool:
    """Check if data starts with the TGV1 magic."""
    return len(data) >= 4 and data[:4] == HEADER_MAGIC
