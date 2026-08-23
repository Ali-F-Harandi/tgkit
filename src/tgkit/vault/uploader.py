"""VaultUploader — chunked encrypted upload to Telegram.

Pipeline per chunk:
    1. Read raw chunk from file
    2. Compress (optional, smart skip)
    3. Encrypt (optional) with deterministic IV = (chunk_index).to_bytes(12, "big")
    4. Prepend TGV1 40-byte header
    5. sendDocument with reply_to=prev_msg_id (reply chain)
    6. Save resume state after each chunk (includes the ENCRYPTION SALT —
       resuming with a fresh random salt would corrupt the file, which is
       exactly the bug tg-vault had)

After all chunks:
    7. Send manifest message (text)
    8. Log to DB (library table)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pyrogram.errors import FloodWait

from tgkit.config.schema import Config
from tgkit.transport.bot_pool import AsyncBotPool
from tgkit.transport.bot import Bot
from tgkit.utils import stable_channel_db_id
from tgkit.vault.crypto import Encryptor
from tgkit.vault.chunk_header import (
    create_header, FLAG_COMPRESSED, FLAG_ENCRYPTED, HEADER_SIZE,
)
from tgkit.vault.compression import compress_data
from tgkit.vault.manifest import Manifest, build_manifest_text
from tgkit.models.link import build_link
from tgkit.db.connection import Database
from tgkit.db.store import LibraryStore

logger = logging.getLogger(__name__)


class VaultUploader:
    """Chunked encrypted uploader.

    Usage:
        uploader = VaultUploader(pool, config, db)
        result = await uploader.upload(
            file_path="/path/to/file.zip",
            dest_channel=-100...,
            encrypt=True,
            password="my_password",
        )
        # result.share_link, result.manifest_msg_id, result.message_ids
    """

    def __init__(self, pool: AsyncBotPool, config: Config, db: Database | None = None):
        self.pool = pool
        self.config = config
        self.db = db

    async def upload(
        self,
        file_path: str | Path,
        dest_channel: int | str,
        encrypt: bool = False,
        password: str | None = None,
        compress: bool = True,
        description: str = "",
        resume: bool = False,
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> dict[str, Any]:
        """Upload a file as chunked vault.

        Args:
            file_path: Local file to upload
            dest_channel: Destination channel
            encrypt: Whether to encrypt
            password: Password (required if encrypt=True)
            compress: Whether to compress (smart skip for already-compressed)
            description: Optional description message (sent before chunks)
            resume: If True, continue an interrupted upload (state file
                ``<filename>.vault_resume.json`` next to the file)
            on_progress: Callback(chunk_index, total_chunks, bytes_uploaded)

        Returns:
            dict with: share_link, manifest_msg_id, message_ids, sha256, size
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = file_path.stat().st_size
        chunk_size = self.config.vault.chunk_size_mb * 1024 * 1024
        total_chunks = max(1, (file_size + chunk_size - 1) // chunk_size)

        # Compute SHA256
        sha256 = await asyncio.to_thread(self._compute_sha256, file_path)
        sha256_prefix = bytes.fromhex(sha256[:32])  # first 16 bytes

        # ── Resume state ──
        resume_path = file_path.with_name(file_path.name + ".vault_resume.json")
        message_ids: list[int] = []
        prev_msg_id: int | None = None
        start_chunk = 0
        session_id = uuid.uuid4().hex[:8]
        resumed = False

        if resume and resume_path.exists():
            try:
                state = json.loads(resume_path.read_text(encoding="utf-8"))
                if state.get("sha256") == sha256 and state.get("name") == file_path.name:
                    message_ids = list(state.get("message_ids", []))
                    if len(message_ids) >= total_chunks:
                        logger.info("All chunks already uploaded — sending manifest only")
                        start_chunk = total_chunks
                    elif message_ids:
                        start_chunk = len(message_ids)
                        session_id = state.get("session_id", session_id)
                        logger.info(f"Resuming upload from chunk {start_chunk}/{total_chunks}")
                    resumed = True
                    # Keep the ORIGINAL encryption salt from the resume state
                    # (creating a new Encryptor with a fresh random salt here
                    # would make the file undecryptable — tg-vault bug fixed)
                else:
                    logger.warning("Resume state mismatch (file changed) — starting fresh")
            except Exception as e:
                logger.warning(f"Failed to read resume state ({e}) — starting fresh")

        # Setup encryption
        encryptor = None
        password_hash = ""
        encryption_salt = ""
        if encrypt:
            if not password:
                raise ValueError("Password required for encryption")
            if resumed and state.get("encryption_salt"):
                # Reuse the salt from the resume state (see above)
                salt = Encryptor.salt_from_str(state["encryption_salt"])
                encryptor = Encryptor(password, salt=salt)
                encryption_salt = Encryptor.salt_to_str(encryptor.salt)
                password_hash = Encryptor.get_password_hash(password)
                if state.get("password_hash") and password_hash != state["password_hash"]:
                    raise ValueError(
                        "Wrong password: does not match the password used for "
                        "the already-uploaded chunks"
                    )
            else:
                encryptor = Encryptor(password)
                encryption_salt = Encryptor.salt_to_str(encryptor.salt)
                password_hash = Encryptor.get_password_hash(password)
        elif resumed and state.get("encryption_salt"):
            # State was created WITH encryption but caller resumed WITHOUT it
            raise ValueError(
                "This upload was started WITH encryption — resume with "
                "the same --encrypt flag and password"
            )

        logger.info(
            f"VaultUploader: {file_path.name} ({file_size:,} bytes) → "
            f"{total_chunks} chunks (chunk_size={chunk_size:,}), "
            f"encrypt={'yes' if encrypt else 'no'}, compress={'yes' if compress else 'no'}"
        )

        # Send description message (only when starting fresh — on resume the
        # description message already exists in the reply chain)
        bot = await self.pool.get_next()
        if description and start_chunk == 0:
            desc_msg = await bot.call(
                lambda: bot.client.raw.send_message(
                    chat_id=dest_channel,
                    text=f"📝 {description}",
                    disable_notification=True,
                )
            )
            prev_msg_id = desc_msg.id
        elif message_ids:
            prev_msg_id = message_ids[-1]

        # Upload chunks (skipping already-uploaded ones on resume)
        bytes_uploaded = start_chunk * chunk_size
        for chunk_index in range(start_chunk, total_chunks):
            start = chunk_index * chunk_size
            end = min(start + chunk_size, file_size)

            # Read chunk
            chunk_data = await asyncio.to_thread(self._read_chunk, file_path, start, end)
            bytes_uploaded += len(chunk_data)

            # Process chunk: compress → encrypt → header
            processed = chunk_data
            flags = 0
            was_compressed = False

            if compress:
                processed, was_compressed = compress_data(processed, file_path.name)
                if was_compressed:
                    flags |= FLAG_COMPRESSED

            if encryptor:
                iv = chunk_index.to_bytes(12, "big")
                processed = encryptor.encrypt_chunk_with_iv(processed, iv)
                flags |= FLAG_ENCRYPTED

            # Prepend TGV1 header
            header = create_header(
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                original_size=len(chunk_data),
                sha256_prefix=sha256_prefix,
                flags=flags,
            )
            chunk_with_header = header + processed

            # Upload chunk as document
            chunk_filename = f"{file_path.name}.part{chunk_index:04d}of{total_chunks:04d}"
            temp_path = Path(f"/tmp/tgkit_chunk_{session_id}_{chunk_index:04d}")
            await asyncio.to_thread(self._write_temp, temp_path, chunk_with_header)

            # Retry with different bots on FloodWait
            upload_msg = None
            for attempt in range(4):
                try:
                    bot = await self.pool.get_next()
                    upload_msg = await bot.call(
                        lambda: bot.client.raw.send_document(
                            chat_id=dest_channel,
                            document=str(temp_path),
                            caption=f"📦 Part {chunk_index + 1}/{total_chunks} | {file_path.name} | #{session_id}",
                            disable_notification=True,
                            reply_to_message_id=prev_msg_id,
                        )
                    )
                    break
                except FloodWait as e:
                    logger.warning(f"Chunk {chunk_index} FloodWait {e.value}s")
                    await asyncio.sleep(e.value + 1)
                    continue

            if upload_msg is None:
                raise RuntimeError(f"Failed to upload chunk {chunk_index} after retries")

            # Clean up temp file
            temp_path.unlink(missing_ok=True)

            message_ids.append(upload_msg.id)
            prev_msg_id = upload_msg.id

            # Persist resume state after EVERY chunk. The encryption salt is
            # stored here so an interrupted encrypted upload can be resumed
            # with the SAME key (fixes the tg-vault corruption bug).
            self._save_resume(
                resume_path,
                name=file_path.name,
                sha256=sha256,
                message_ids=message_ids,
                session_id=session_id,
                encryption_salt=encryption_salt,
                password_hash=password_hash,
                compress=compress,
                dest_channel=dest_channel,
            )

            if on_progress:
                on_progress(chunk_index + 1, total_chunks, bytes_uploaded)

            logger.debug(f"Chunk {chunk_index + 1}/{total_chunks} uploaded (msg_id={upload_msg.id})")

        # Send manifest
        manifest = Manifest(
            name=file_path.name,
            size=file_size,
            sha256=sha256,
            total_parts=total_chunks,
            chunk_size=chunk_size,
            message_ids=message_ids,
            compressed=compress,
            encrypted=bool(encryptor),
            has_chunk_header=True,
            date=datetime.now(timezone.utc).isoformat(),
            session_id=session_id,
            encryption_salt=encryption_salt,
            encryption_algorithm="aes-256-gcm" if encryptor else "",
            encryption_kdf="pbkdf2-sha512-600k" if encryptor else "",
            password_hash=password_hash,
        )

        manifest_text = build_manifest_text(manifest)
        manifest_msg = await bot.call(
            lambda: bot.client.raw.send_message(
                chat_id=dest_channel,
                text=manifest_text,
                disable_notification=True,
                reply_to_message_id=prev_msg_id,
            )
        )

        share_link = build_link(dest_channel, manifest_msg.id)

        # Upload finished — remove resume state
        resume_path.unlink(missing_ok=True)

        logger.info(f"Vault upload complete: {share_link} ({total_chunks} chunks)")

        # Log to DB
        if self.db:
            store = LibraryStore(self.db)
            dest_db_id = stable_channel_db_id(dest_channel)
            from tgkit.db.store import ChannelStore
            ch_store = ChannelStore(self.db)
            if not ch_store.get(dest_db_id):
                ch_store.upsert(channel_id=dest_db_id, role="destination")
            store.insert(
                name=file_path.name,
                size=file_size,
                sha256=sha256,
                total_parts=total_chunks,
                chunk_size=chunk_size,
                message_ids=message_ids,
                main_channel=dest_db_id,
                share_link=share_link,
                kind="vault",
                description=description,
                manifest_msg_id=manifest_msg.id,
                encrypted=bool(encryptor),
                compressed=compress,
                has_chunk_header=True,
                encryption_salt=encryption_salt or None,
                password_hash=password_hash or None,
                original_size=file_size,
                session_id=session_id,
            )

        return {
            "share_link": share_link,
            "manifest_msg_id": manifest_msg.id,
            "message_ids": message_ids,
            "sha256": sha256,
            "size": file_size,
            "total_chunks": total_chunks,
            "encrypted": bool(encryptor),
            "compressed": compress,
        }

    @staticmethod
    def _save_resume(
        path: Path,
        name: str,
        sha256: str,
        message_ids: list[int],
        session_id: str,
        encryption_salt: str,
        password_hash: str,
        compress: bool,
        dest_channel: int | str,
    ) -> None:
        """Persist resume state (atomic write — tmp file + rename)."""
        state = {
            "name": name,
            "sha256": sha256,
            "message_ids": message_ids,
            "session_id": session_id,
            "encryption_salt": encryption_salt,
            "password_hash": password_hash,
            "compress": compress,
            "dest_channel": str(dest_channel),
            "saved_at": time.time(),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        """Compute SHA256 of a file (streaming)."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8 * 1024 * 1024)  # 8 MB
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _read_chunk(file_path: Path, start: int, end: int) -> bytes:
        """Read a chunk from file [start:end]."""
        with open(file_path, "rb") as f:
            f.seek(start)
            return f.read(end - start)

    @staticmethod
    def _write_temp(path: Path, data: bytes) -> None:
        """Write data to a temp file."""
        path.write_bytes(data)
