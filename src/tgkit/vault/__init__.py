"""Vault subsystem — chunked encrypted storage on Telegram.

Ports the proven design from tg-vault:
    - AES-256-GCM encryption (PBKDF2-HMAC-SHA512, 600k iters)
    - TGV1 40-byte self-describing chunk header
    - Smart gzip compression (skip already-compressed formats)
    - Chunked upload with reply-chain + manifest
    - Parallel chunked download with SHA256 verify
    - Resume capability
"""

from __future__ import annotations

from tgkit.vault.crypto import Encryptor
from tgkit.vault.chunk_header import (
    create_header, parse_header, is_chunk_with_header,
    FLAG_COMPRESSED, FLAG_ENCRYPTED, HEADER_MAGIC, HEADER_SIZE,
)
from tgkit.vault.compression import (
    compress_data, decompress_data, should_skip_compression,
)
from tgkit.vault.manifest import Manifest, parse_manifest, build_manifest_text
from tgkit.vault.uploader import VaultUploader
from tgkit.vault.downloader import VaultDownloader

__all__ = [
    "Encryptor",
    "create_header", "parse_header", "is_chunk_with_header",
    "FLAG_COMPRESSED", "FLAG_ENCRYPTED", "HEADER_MAGIC", "HEADER_SIZE",
    "compress_data", "decompress_data", "should_skip_compression",
    "Manifest", "parse_manifest", "build_manifest_text",
    "VaultUploader", "VaultDownloader",
]
