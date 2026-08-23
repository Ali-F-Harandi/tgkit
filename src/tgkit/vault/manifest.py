"""Manifest — metadata message that describes a vault file.

The manifest is sent as the LAST message in the chunk reply-chain.
Its share link is what users share to download the file.

Format (text message, editable via editMessageText):
    TGKIT_MANIFEST|name|parts|sha256_prefix[:16]
    {compact JSON}

JSON fields:
    name: original filename
    size: original file size in bytes
    sha256: full SHA256 of original file
    total_parts: number of chunks
    chunk_size: size of each chunk (before compression/encryption)
    message_ids: [list of chunk message IDs]
    compressed: bool
    encrypted: bool
    has_chunk_header: bool (always true for tgkit)
    date: ISO timestamp
    session_id: UUID hex (for multi-process isolation)

    If encrypted:
        encryption_salt: base64
        encryption_algorithm: "aes-256-gcm"
        encryption_kdf: "pbkdf2-sha512-600k"
        password_hash: hex (for fail-fast verification)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

MANIFEST_PREFIX = "TGKIT_MANIFEST"


@dataclass
class Manifest:
    """Vault file manifest."""
    name: str
    size: int
    sha256: str
    total_parts: int
    chunk_size: int
    message_ids: list[int] = field(default_factory=list)
    compressed: bool = False
    encrypted: bool = False
    has_chunk_header: bool = True
    date: str = ""
    session_id: str = ""

    # Encryption fields (only if encrypted=True)
    encryption_salt: str = ""
    encryption_algorithm: str = ""
    encryption_kdf: str = ""
    password_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "sha256": self.sha256,
            "total_parts": self.total_parts,
            "chunk_size": self.chunk_size,
            "message_ids": self.message_ids,
            "compressed": self.compressed,
            "encrypted": self.encrypted,
            "has_chunk_header": self.has_chunk_header,
            "date": self.date,
            "session_id": self.session_id,
            "encryption_salt": self.encryption_salt,
            "encryption_algorithm": self.encryption_algorithm,
            "encryption_kdf": self.encryption_kdf,
            "password_hash": self.password_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Manifest":
        return cls(
            name=d.get("name", ""),
            size=int(d.get("size", 0)),
            sha256=d.get("sha256", ""),
            total_parts=int(d.get("total_parts", 0)),
            chunk_size=int(d.get("chunk_size", 0)),
            message_ids=list(d.get("message_ids", [])),
            compressed=bool(d.get("compressed", False)),
            encrypted=bool(d.get("encrypted", False)),
            has_chunk_header=bool(d.get("has_chunk_header", True)),
            date=d.get("date", ""),
            session_id=d.get("session_id", ""),
            encryption_salt=d.get("encryption_salt", ""),
            encryption_algorithm=d.get("encryption_algorithm", ""),
            encryption_kdf=d.get("encryption_kdf", ""),
            password_hash=d.get("password_hash", ""),
        )


def build_manifest_text(manifest: Manifest) -> str:
    """Build the text message for the manifest.

    Format:
        TGKIT_MANIFEST|name|parts|sha256_prefix[:16]
        {compact JSON}
    """
    sha256_prefix = manifest.sha256[:16] if manifest.sha256 else ""
    header = f"{MANIFEST_PREFIX}|{manifest.name}|{manifest.total_parts}|{sha256_prefix}"
    body = json.dumps(manifest.to_dict(), separators=(",", ":"), ensure_ascii=False)
    return f"{header}\n{body}"


def parse_manifest(text: str) -> Manifest | None:
    """Parse a manifest from text message.

    Returns None if the text is not a valid manifest.
    """
    if not text or not text.startswith(MANIFEST_PREFIX):
        return None

    lines = text.split("\n", 1)
    if len(lines) < 2:
        return None

    try:
        body = json.loads(lines[1])
        return Manifest.from_dict(body)
    except (json.JSONDecodeError, KeyError):
        return None
