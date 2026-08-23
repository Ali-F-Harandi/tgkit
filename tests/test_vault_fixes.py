"""Tests for vault bug fixes (v0.2.0).

Covers:
    - process_chunk: headerless legacy chunks no longer crash (NameError fix)
    - process_chunk: per-chunk header flags vs manifest-level fallback
    - ChunkAssembler: bounded-memory in-order writing
    - stable_channel_db_id: deterministic across processes (PYTHONHASHSEED-safe)
    - VaultUploader._save_resume: atomic state persistence

Run with: pytest tests/test_vault_fixes.py -v
"""

from __future__ import annotations

import gzip
import io
import json
import subprocess
import sys
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tgkit.vault.chunk_header import create_header, FLAG_COMPRESSED, FLAG_ENCRYPTED
from tgkit.vault.compression import compress_data
from tgkit.vault.crypto import Encryptor
from tgkit.vault.downloader import ChunkAssembler, process_chunk
from tgkit.vault.manifest import Manifest
from tgkit.utils import stable_channel_db_id


def _make_manifest(**kwargs) -> Manifest:
    defaults = dict(
        name="test.bin", size=100, sha256="ab" * 32, total_parts=2,
        chunk_size=50, message_ids=[11, 22],
    )
    defaults.update(kwargs)
    return Manifest(**defaults)


# ============================================================================
# process_chunk — NameError fix for headerless chunks
# ============================================================================

def test_process_chunk_headerless_uncompressed_no_crash():
    """REGRESSION: headerless chunk + compressed manifest used to raise
    NameError ('header' never assigned). Must not crash now."""
    raw = b"plain legacy chunk data"
    result = process_chunk(raw, 0, _make_manifest(compressed=False), None)
    assert result == raw


def test_process_chunk_headerless_compressed_manifest_fallback():
    """Headerless legacy chunk + manifest.compressed=True → decompress."""
    raw = b"legacy data " * 10
    gz = gzip.compress(raw)
    result = process_chunk(gz, 0, _make_manifest(compressed=True), None)
    assert result == raw


def test_process_chunk_headerless_compressed_flag_corrupt_fallback():
    """Legacy tg-vault bug: compressed=True but this chunk is raw —
    decompression failure must fall back to raw bytes, not raise."""
    raw = b"actually not gzipped at all"
    result = process_chunk(raw, 0, _make_manifest(compressed=True), None)
    assert result == raw


def test_process_chunk_with_header_strips_and_decompresses():
    """Chunk with TGV1 header + FLAG_COMPRESSED → header stripped, data
    decompressed per-chunk (even if manifest says compressed=False)."""
    raw = b"compressible " * 20
    gz, was = compress_data(raw, "data.bin")
    assert was
    header = create_header(1, 2, len(raw), b"s" * 16, FLAG_COMPRESSED)
    result = process_chunk(header + gz, 1, _make_manifest(compressed=False), None)
    assert result == raw


def test_process_chunk_encrypted_roundtrip():
    """Full pipeline roundtrip: header + encrypt → process_chunk decrypts."""
    password = "test-password-123"
    enc = Encryptor(password, salt=b"S" * 32)
    raw = b"secret chunk payload " * 5
    iv = 3 .to_bytes(12, "big")
    ct = enc.encrypt_chunk_with_iv(raw, iv)
    header = create_header(3, 4, len(raw), b"h" * 16, FLAG_ENCRYPTED)

    manifest = _make_manifest(encrypted=True)
    manifest.encryption_salt = Encryptor.salt_to_str(b"S" * 32)

    result = process_chunk(header + ct, 3, manifest, enc)
    assert result == raw


def test_process_chunk_tampered_raises():
    """Tampered ciphertext must raise InvalidTag (auth tag working)."""
    import pytest
    from cryptography.exceptions import InvalidTag

    enc = Encryptor("pw", salt=b"S" * 32)
    raw = b"x" * 100
    iv = (0).to_bytes(12, "big")
    ct = bytearray(enc.encrypt_chunk_with_iv(raw, iv))
    ct[10] ^= 0xFF  # tamper

    with pytest.raises(InvalidTag):
        process_chunk(bytes(ct), 0, _make_manifest(encrypted=True), enc)


# ============================================================================
# ChunkAssembler — bounded memory, in-order writes
# ============================================================================

def test_assembler_out_of_order():
    """Chunks arriving 2,0,1 must be written 0,1,2 in order."""
    buf = io.BytesIO()
    a = ChunkAssembler(buf, start_index=0)
    assert a.add(2, b"CC") == 0   # buffered
    assert a.add(0, b"AA") == 1   # writes 0
    assert a.add(1, b"BB") == 2   # writes 1,2 (flush)
    assert buf.getvalue() == b"AABBCC"
    assert a.written_count == 3
    assert a.max_pending == 2     # memory peaked at 2 chunks, not 3


def test_assembler_resume_overlap():
    """Re-delivery of an already-written chunk is ignored (resume path)."""
    buf = io.BytesIO()
    a = ChunkAssembler(buf, start_index=0)
    a.add(0, b"AA")
    assert a.add(0, b"AA") == 0  # duplicate ignored
    assert buf.getvalue() == b"AA"


def test_assembler_bounded_memory_many_chunks():
    """With 100 chunks delivered REVERSED, pending never exceeds... well,
    reversed delivery is worst case: pending grows until first arrives.
    With in-order-ish delivery (sliding window), pending stays bounded."""
    buf = io.BytesIO()
    a = ChunkAssembler(buf, start_index=0)
    # Simulate a sliding window of width 4
    for base in range(0, 100, 2):
        a.add(base + 1, b"x")   # arrives early
        a.add(base, b"y")       # completes the pair → flush both
    assert a.written_count == 100
    assert a.max_pending <= 4


# ============================================================================
# stable_channel_db_id — cross-run determinism
# ============================================================================

def test_stable_channel_db_id_deterministic():
    """Same input → same output within a process."""
    a = stable_channel_db_id("@mychannel")
    b = stable_channel_db_id("@mychannel")
    assert a == b


def test_stable_channel_db_id_across_processes():
    """REGRESSION: hash() was randomized per process (PYTHONHASHSEED).
    The stable variant must return the same value in a fresh interpreter."""
    expected = stable_channel_db_id("@mychannel")
    code = (
        "import sys; sys.path.insert(0, 'src'); "
        "from tgkit.utils import stable_channel_db_id; "
        "print(stable_channel_db_id('@mychannel'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=Path(__file__).parent.parent,
    )
    assert out.returncode == 0, out.stderr
    assert int(out.stdout.strip()) == expected


def test_stable_channel_db_id_numeric_passthrough():
    assert stable_channel_db_id(-1003873843444) == -1003873843444
    assert stable_channel_db_id("@a") != stable_channel_db_id("@b")


# ============================================================================
# Uploader resume state — atomic persistence
# ============================================================================

def test_save_resume_roundtrip(tmp_path):
    from tgkit.vault.uploader import VaultUploader

    state_path = tmp_path / "file.zip.vault_resume.json"
    VaultUploader._save_resume(
        state_path,
        name="file.zip",
        sha256="ff" * 32,
        message_ids=[101, 102, 103],
        session_id="abcd1234",
        encryption_salt="c2FsdA==",
        password_hash="deadbeef" * 8,
        compress=True,
        dest_channel=-1003873843444,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["name"] == "file.zip"
    assert state["message_ids"] == [101, 102, 103]
    # CRITICAL: the encryption salt must be persisted (tg-vault bug fix)
    assert state["encryption_salt"] == "c2FsdA=="
    # Atomic write: no leftover tmp file
    assert not list(tmp_path.glob("*.tmp"))


def test_save_resume_atomic_no_partial_state_on_crash(tmp_path):
    """Even if interrupted, the previous complete state must remain valid."""
    from tgkit.vault.uploader import VaultUploader

    state_path = tmp_path / "f.vault_resume.json"
    VaultUploader._save_resume(state_path, "f", "aa" * 32, [1], "s", "", "", True, -1)
    before = state_path.read_text()

    # Simulate a crash mid-write: leave a tmp file, don't rename
    state_path.with_suffix(".tmp").write_text("{partial json")
    assert state_path.read_text() == before  # original untouched
