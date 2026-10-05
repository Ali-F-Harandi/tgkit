"""Tests for the smart size-routing layer (v0.3.0).

Covers:
    - human_size: byte formatting for humans
    - download_route / upload_route: Bot API vs MTProto vs vault vs impossible
    - download_note / upload_note: console guidance text mentions the right fix
    - _token_looks_ok (doctor): bot token format sanity

The whole point of this layer: "first 10 files work, file #11 (122 MB)
explodes" must become impossible. Run with: pytest tests/test_limits.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tgkit.limits import (
    BOT_API_MAX_DOWNLOAD,
    BOT_API_MAX_UPLOAD,
    MTPROTO_MAX,
    VAULT_CHUNK_MB,
    human_size,
    download_route,
    upload_route,
    download_note,
    upload_note,
)

MB = 1024 * 1024
GB = 1024 * 1024 * 1024


# ── human_size ─────────────────────────────────────────────────────

def test_human_size_bytes():
    assert human_size(0) == "0 B"
    assert human_size(512) == "512 B"
    assert human_size(None) == "0 B"


def test_human_size_kb_mb_gb():
    assert human_size(1536) == "1.5 KB"
    assert human_size(17 * MB) == "17.0 MB"
    assert human_size(122 * MB) == "122.0 MB"
    assert human_size(1.5 * GB) == "1.5 GB"


# ── download routing ───────────────────────────────────────────────

def test_small_file_downloads_via_bot_api():
    assert download_route(0) == "bot_api"
    assert download_route(5 * MB) == "bot_api"
    assert download_route(BOT_API_MAX_DOWNLOAD) == "bot_api"      # exactly 20 MB: still fine


def test_big_file_requires_mtproto():
    # The "file 11" scenario: 122 MB over the Bot API's 20 MB cap
    assert download_route(BOT_API_MAX_DOWNLOAD + 1) == "mtproto"
    assert download_route(122 * MB) == "mtproto"
    assert download_route(2 * GB) == "mtproto"                    # exactly 2 GB: still fine


def test_over_2gb_is_impossible():
    assert download_route(2 * GB + 1) == "impossible"
    assert download_route(4 * GB) == "impossible"


# ── upload routing ─────────────────────────────────────────────────

def test_small_upload_via_bot_api():
    assert upload_route(30 * MB) == "bot_api"
    assert upload_route(BOT_API_MAX_UPLOAD) == "bot_api"          # exactly 50 MB: still fine


def test_big_upload_via_mtproto():
    assert upload_route(BOT_API_MAX_UPLOAD + 1) == "mtproto"
    assert upload_route(500 * MB) == "mtproto"
    assert upload_route(2 * GB) == "mtproto"


def test_huge_upload_needs_vault():
    assert upload_route(2 * GB + 1) == "vault"
    assert upload_route(10 * GB) == "vault"


# ── console notes ──────────────────────────────────────────────────

def test_download_note_mentions_mtproto_for_big_files():
    note = download_note(122 * MB)
    assert note is not None
    assert "20 MB" in note
    assert "MTProto" in note


def test_download_note_silent_for_small_files():
    assert download_note(5 * MB) is None
    assert download_note(None) is None


def test_download_note_explains_impossible():
    note = download_note(3 * GB)
    assert "2 GB" in note


def test_upload_note_mentions_mtproto_and_vault():
    assert "MTProto" in upload_note(100 * MB)
    assert download_note(5 * MB) is None
    vault_note = upload_note(3 * GB)
    assert "vault" in vault_note.lower()
    assert "2 GB" in vault_note


def test_chunk_default_is_under_all_caps():
    # Vault chunks must fit under BOTH Bot API and MTProto ceilings.
    assert VAULT_CHUNK_MB * MB < BOT_API_MAX_DOWNLOAD
    assert VAULT_CHUNK_MB * MB < MTPROTO_MAX


# ── doctor token sanity ────────────────────────────────────────────

def test_token_looks_ok():
    from tgkit.commands.doctor import _token_looks_ok
    assert _token_looks_ok("8866132706:AAGSmU4xNBLgoqi9ceYapHi-PEB57MvDm3I")
    assert not _token_looks_ok("not-a-token")
    assert not _token_looks_ok("12345")                            # no colon part
    assert not _token_looks_ok("abc:short")                        # secret too short


# ── fetch helpers (pure parts) ─────────────────────────────────────

def test_fetch_helpers_importable_and_pure():
    from tgkit.commands.fetch import _looks_like_manifest, _filename_for, _unique_path

    class FakeDoc:
        file_name = "TGKIT_MANIFEST|backup.zip|7|abcd1234"

    class FakeMsg:
        caption = "TGKIT_MANIFEST|backup.zip|7|abcd1234"
        document = FakeDoc()
        id = 42

    assert _looks_like_manifest(FakeMsg) is True

    class PlainMsg:
        caption = "just a caption"
        document = None
        id = 43

    assert _looks_like_manifest(PlainMsg) is False

    # filename derivation
    class Named:
        file_name = "report.zip"

    assert _filename_for(FakeMsg, Named()) == "report.zip"

    class Photo:
        pass

    assert _filename_for(FakeMsg, Photo()) == "photo_42.jpg"


def test_unique_path_never_overwrites(tmp_path):
    from tgkit.commands.fetch import _unique_path

    first = tmp_path / "foo.zip"
    first.write_bytes(b"x")
    second = _unique_path(first)
    assert second.name == "foo (1).zip"
    second.write_bytes(b"x")
    third = _unique_path(first)
    assert third.name == "foo (2).zip"
