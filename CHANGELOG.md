# Changelog

All notable changes to tgkit are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] — 2026-08-23

Live-tested end-to-end against a real channel (5 bots, MTProto Tier 2):
encrypted 10-chunk upload → surgical resume from 7/10 chunks (salt reuse
verified by SHA256 round-trip) → parallel download → wrong-password fail-fast
→ `--from-scan` copy without forward header → DB sync.

### Fixed

- **`tgkit --version` showed a stale hardcoded version** — `__version__` is
  now read from installed package metadata (single source of truth:
  `pyproject.toml`), with a fallback for source checkouts.
- **vault uploads didn't persist crypto metadata to the library DB** —
  `manifest_msg_id`, `encrypted`, `compressed`, `has_chunk_header`,
  `encryption_salt`, `original_size` and `session_id` were silently dropped,
  leaving `encrypted=0` records for encrypted files. `LibraryStore.insert`
  now accepts and persists all of them, and a re-upload of the same SHA256
  refreshes the flags instead of leaving stale values behind.
- **DB schema**: added `library.password_hash` column via migration v2
  (additive `ALTER TABLE`, applied automatically on `Database.init()`; fresh
  and legacy databases both covered) — enables offline resume verification.

### Changed

- Test suite grew to 56 tests (library persistence, flag refresh on
  re-upload, and migration v2 regression tests).

### Known issues

- pyrofork occasionally prints a cosmetic `sqlite3.ProgrammingError:
  Cannot operate on a closed database` traceback at process exit when 5
  clients flush their session files simultaneously. Results are unaffected;
  the error comes from pyrofork's exit-time teardown, outside tgkit's
  control.

## [0.2.0] — 2026-08-23

### Fixed

- **vault downloader: NameError on legacy headerless chunks** — the `header`
  variable was only assigned inside `if is_chunk_with_header(data):` but read
  unconditionally afterwards. A chunk without a TGV1 header crashed the
  download after 4 retries. Headerless (tg-vault legacy) chunks now fall back
  to the manifest-level `compressed` flag.
- **vault downloader: entire file buffered in RAM** — all chunks were held in
  a dict before writing. A 2 GB vault file required 2 GB+ of memory. Chunks
  are now written to disk in order as soon as they become contiguous
  (`ChunkAssembler`), bounding memory to a few in-flight chunks.
- **vault downloader: decompression failure on legacy chunks** — tg-vault v7
  set `compressed=true` even when a specific chunk wasn't actually gzip
  compressed. Decompression now falls back to raw bytes instead of failing
  the whole download.
- **vault uploader: no resume** — the docstring promised resume support but
  it was never implemented. Uploads now persist `<file>.vault_resume.json`
  after every chunk (atomically, via tmp-file + rename) and `--resume`
  continues from the last uploaded chunk.
- **vault uploader: resume + encryption corruption (inherited tg-vault bug)**
  — the resume state now stores the **encryption salt** and password
  verification hash. Resuming an encrypted upload reuses the ORIGINAL salt
  (a fresh random salt would leave the file permanently undecryptable) and
  rejects a wrong password before uploading anything.
- **vault downloader: resume** — partial downloads keep a `.downloading`
  temp file; `--resume` continues from the last complete chunk. Completed
  chunks are derived from the **manifest** chunk size, never the local
  config (which may have changed since the upload) — another inherited
  tg-vault bug avoided.
- **unstable channel DB IDs (systemic)** — 9 call sites used Python's
  built-in `hash()`, which is randomized per process (`PYTHONHASHSEED`).
  Channel records created in one run would never match lookups in the next
  run for `@username` channels. All call sites now use
  `stable_channel_db_id()` (SHA256-based, moved to `tgkit.utils`).
- **migrate: hardcoded absolute paths** — legacy tg-vault discovery now
  checks `$TG_VAULT_DIR`, `~/tg-vault`, `./tg-vault` instead of a single
  hardcoded machine-specific path.
- **security: removed a bundled helper script containing a live bot token**
  from the repository tree.

### Added

- `tgkit vault upload --resume / -r` flag.
- `tgkit vault download --resume / -r` flag.
- `tgkit.utils.stable_channel_db_id()` — shared, importable from anywhere
  (`tgkit.scan.parallel_scanner` re-exports it for backward compatibility).
- `ChunkAssembler` and `process_chunk()` extracted as testable units.
- 14 new regression tests (`tests/test_vault_fixes.py`) covering the fixes
  above, including cross-process stability and tamper detection.

### Changed

- Bumped version to 0.2.0.
- Vault resume state files (`*.vault_resume.json`) and temp download files
  (`*.downloading`) added to `.gitignore`.

## [0.1.0] — 2026-07-21

### Added

- Initial release: scan (batched + parallel-ranges), copy via `file_id`,
  forward, reupload, linked-list posts, encrypted chunked vault
  (AES-256-GCM + PBKDF2-SHA512 600k + TGV1 headers), SQLite (WAL + FTS5)
  with Telegram-channel sync, two-tier capability detection, adaptive
  per-bot throttling with per-channel cooldown, `tgkit migrate` from
  tg-vault, interactive REPL.
