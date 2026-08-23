"""VaultDownloader — parallel chunked download from Telegram.

Pipeline:
    1. Fetch manifest message → parse Manifest
    2. Download chunks in parallel (round-robin bots), writing to disk
       in-order as soon as contiguous chunks are available (bounded memory)
    3. For each chunk: strip TGV1 header → decrypt → decompress
    4. Verify SHA256, rename temp file to final output

Resume:
    Partial downloads are kept as ``<output>.downloading``. Re-running with
    ``resume=True`` skips chunks already written to disk. The number of
    completed chunks is derived from the file size divided by the chunk size
    recorded in the MANIFEST (never the local config — the config may have
    changed since the upload).

Legacy compatibility (tg-vault):
    - Headerless chunks (no TGV1 magic) fall back to manifest-level
      ``compressed`` flag instead of crashing with NameError.
    - Decompression failures fall back to raw bytes (tg-vault v7 had a bug
      that set compressed=true even when a chunk wasn't actually compressed).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, BinaryIO, Callable

from pyrogram.errors import FloodWait

from tgkit.config.schema import Config
from tgkit.transport.bot_pool import AsyncBotPool
from tgkit.vault.crypto import Encryptor
from tgkit.vault.chunk_header import parse_header, is_chunk_with_header, HEADER_SIZE
from tgkit.vault.compression import decompress_data
from tgkit.vault.manifest import Manifest, parse_manifest, MANIFEST_PREFIX
from tgkit.models.link import parse_link, build_link

logger = logging.getLogger(__name__)


class ChunkAssembler:
    """Write chunks to a file in order, buffering only out-of-order ones.

    Memory is bounded by ~``max_pending`` chunks regardless of file size
    (the previous implementation buffered the ENTIRE file in RAM).
    """

    def __init__(self, out_file: BinaryIO, start_index: int = 0):
        self.out_file = out_file
        self.next_index = start_index
        self.pending: dict[int, bytes] = {}
        self.max_pending = 0

    def add(self, index: int, data: bytes) -> int:
        """Add a processed chunk. Writes every contiguous chunk to disk.

        Returns the number of chunks written by this call.
        """
        if index < self.next_index:
            return 0  # already written (resume overlap)
        self.pending[index] = data
        self.max_pending = max(self.max_pending, len(self.pending))
        written = 0
        while self.next_index in self.pending:
            chunk = self.pending.pop(self.next_index)
            self.out_file.write(chunk)
            self.next_index += 1
            written += 1
        return written

    @property
    def written_count(self) -> int:
        return self.next_index


def process_chunk(
    data: bytes,
    chunk_index: int,
    manifest: Manifest,
    encryptor: Encryptor | None,
) -> bytes:
    """Pure chunk post-processing: strip header → decrypt → decompress.

    Kept as a standalone function so it can be unit-tested without network.

    Headerless (legacy tg-vault) chunks fall back to manifest-level flags.
    """
    header = parse_header(data)
    if header is not None:
        data = data[HEADER_SIZE:]

    if encryptor:
        iv = chunk_index.to_bytes(12, "big")
        data = encryptor.decrypt_chunk(data, iv)

    # Per-chunk flag when header present; manifest-level fallback otherwise
    should_decompress = header.is_compressed if header is not None else manifest.compressed
    if should_decompress:
        try:
            data = decompress_data(data, True)
        except Exception:
            # Legacy tg-vault bug: compressed=True even when this chunk
            # wasn't compressed — fall through with raw bytes.
            pass
    return data


class VaultDownloader:
    """Parallel chunked downloader.

    Usage:
        downloader = VaultDownloader(pool, config)
        result = await downloader.download(
            link="https://t.me/c/123/456",
            output_path="/path/to/output.zip",
            password="my_password",  # if encrypted
            resume=True,             # resume a partial download
        )
    """

    def __init__(self, pool: AsyncBotPool, config: Config):
        self.pool = pool
        self.config = config

    async def fetch_manifest(self, link: str) -> Manifest | None:
        """Fetch and parse a manifest from a link."""
        channel_id, msg_id = parse_link(link)
        bot = await self.pool.get_next()

        try:
            msg = await bot.call(
                lambda: bot.client.raw.get_messages(channel_id, msg_id)
            )
            if msg is None or getattr(msg, "empty", False):
                return None

            # Check if it's a text manifest
            text = msg.text or msg.caption or ""
            if text.startswith(MANIFEST_PREFIX):
                return parse_manifest(text)

            # tg-vault legacy manifests (different prefix) are not supported
            # by fetch_manifest — they would need the forwardMessage dance.
            # Could be a file manifest (JSON file)
            if msg.document and msg.document.file_name and msg.document.file_name.endswith(".manifest.json"):
                # Download and parse
                temp_path = Path(f"/tmp/tgkit_manifest_{msg_id}")
                await bot.call(
                    lambda: bot.client.raw.download_media(msg, file_name=str(temp_path))
                )
                text = temp_path.read_text(encoding="utf-8")
                temp_path.unlink(missing_ok=True)
                return parse_manifest(text)

            return None

        except Exception as e:
            logger.error(f"Failed to fetch manifest: {e}")
            return None

    async def download(
        self,
        link: str,
        output_path: str | Path | None = None,
        output_dir: str | Path | None = None,
        password: str | None = None,
        resume: bool = False,
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> dict[str, Any]:
        """Download a vault file.

        Args:
            link: Manifest share link
            output_path: Full output path (mutually exclusive with output_dir)
            output_dir: Output directory (filename from manifest)
            password: Password (required if encrypted)
            resume: If True, continue a partial ``.downloading`` temp file
            on_progress: Callback(chunks_done, total_chunks, bytes_downloaded)

        Returns:
            dict with: output_path, sha256_verified, size, sha256
        """
        # Fetch manifest
        manifest = await self.fetch_manifest(link)
        if not manifest:
            raise ValueError("Failed to fetch or parse manifest")

        logger.info(
            f"VaultDownloader: {manifest.name} ({manifest.size:,} bytes, "
            f"{manifest.total_parts} chunks, encrypted={manifest.encrypted})"
        )

        # Verify password if encrypted
        encryptor = None
        if manifest.encrypted:
            if not password:
                raise ValueError("File is encrypted — password required")
            if manifest.password_hash:
                if not Encryptor.verify_password_hash(password, manifest.password_hash):
                    raise ValueError("Wrong password (verification hash mismatch)")
            salt = Encryptor.salt_from_str(manifest.encryption_salt)
            encryptor = Encryptor(password, salt)

        # Determine output path
        if output_path:
            out_path = Path(output_path)
        elif output_dir:
            out_path = Path(output_dir) / manifest.name
        else:
            out_path = Path(manifest.name)

        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Parse channel from link
        channel_id, _ = parse_link(link)

        # Download chunks in parallel
        total_chunks = manifest.total_parts
        message_ids = manifest.message_ids

        temp_path = Path(str(out_path) + ".downloading")

        # ── Resume: count completed chunks from the MANIFEST chunk size ──
        start_chunk = 0
        mode = "wb"
        if resume and temp_path.exists():
            current_size = temp_path.stat().st_size
            # Guard against chunk_size=0 in malformed manifests
            completed = current_size // manifest.chunk_size if manifest.chunk_size else 0
            if 0 < completed < total_chunks:
                start_chunk = completed
                mode = "ab"
                # Truncate any partial chunk at the tail
                with open(temp_path, "r+b") as tf:
                    tf.truncate(completed * manifest.chunk_size)
                logger.info(f"Resuming download from chunk {start_chunk}/{total_chunks}")
            elif completed >= total_chunks:
                # Looks complete — fall through, will fail SHA verify if wrong
                logger.info("Temp file already covers all chunks; verifying SHA256")
                start_chunk = total_chunks

        chunks_done = start_chunk
        bytes_downloaded = start_chunk * manifest.chunk_size
        write_lock = asyncio.Lock()
        sem = asyncio.Semaphore(self.pool.size)
        assembler: ChunkAssembler | None = None

        if start_chunk < total_chunks:
            f = open(temp_path, mode)
            assembler = ChunkAssembler(f, start_index=start_chunk)
        else:
            f = None

        async def download_chunk(chunk_index: int, msg_id: int) -> None:
            nonlocal chunks_done, bytes_downloaded
            async with sem:
                bot = await self.pool.get_next()
                for attempt in range(4):
                    try:
                        # Download to temp file
                        tmp = Path(f"/tmp/tgkit_dl_{chunk_index:04d}")
                        msg = await bot.call(
                            lambda: bot.client.raw.get_messages(channel_id, msg_id)
                        )
                        await bot.call(
                            lambda: bot.client.raw.download_media(msg, file_name=str(tmp))
                        )

                        data = tmp.read_bytes()
                        tmp.unlink(missing_ok=True)

                        # Strip header → decrypt → decompress (pure function)
                        data = process_chunk(data, chunk_index, manifest, encryptor)

                        async with write_lock:
                            written = assembler.add(chunk_index, data)
                            chunks_done += written
                            bytes_downloaded += len(data)

                            # Avoid blocking the event loop on large writes
                            await asyncio.sleep(0)

                        if on_progress:
                            on_progress(chunks_done, total_chunks, bytes_downloaded)

                        return

                    except FloodWait as e:
                        await asyncio.sleep(e.value + 1)
                        continue
                    except Exception as e:
                        if attempt < 3:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        raise

        try:
            if assembler is not None:
                # Launch all downloads
                tasks = [
                    asyncio.create_task(download_chunk(i, message_ids[i]))
                    for i in range(start_chunk, total_chunks)
                ]
                await asyncio.gather(*tasks)
        finally:
            if f is not None:
                f.close()
                if chunks_done >= total_chunks:
                    os.replace(temp_path, out_path)

        logger.info(f"Wrote {total_chunks} chunks to {out_path}")

        # Verify SHA256
        actual_sha256 = await asyncio.to_thread(self._compute_sha256, out_path)
        verified = (actual_sha256 == manifest.sha256)

        if verified:
            logger.info("SHA256 verified ✓")
        else:
            logger.warning(f"SHA256 MISMATCH! Expected {manifest.sha256}, got {actual_sha256}")

        return {
            "output_path": str(out_path),
            "sha256_verified": verified,
            "size": out_path.stat().st_size,
            "sha256": actual_sha256,
        }

    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8 * 1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
