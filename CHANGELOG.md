# Changelog

All notable changes to tgkit are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
