# Contributing to tgkit

Thanks for your interest in improving tgkit! This document covers the essentials for contributing code, reporting bugs, and submitting pull requests.

## Development setup

```bash
git clone https://github.com/Ali-F-Harandi/tgkit.git
cd tgkit

# Create a virtual environment (Python >= 3.10)
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run the test suite
pytest tests/ -v
```

**Note:** tgkit depends on [pyrofork](https://github.com/telegramplayground/pyrogram)
(NOT vanilla pyrogram — 64-bit channel ID support is required). It is installed
automatically as a dependency.

## Project layout

```
src/tgkit/
├── cli.py               # argparse entry point — command routing only
├── commands/            # one module per command group (scan, vault, ops...)
├── transport/           # BotAPIClient + TgClient (pyrofork MTProto) + pool/throttle
├── operations/          # CopyOp / ForwardOp / ReuploadOp + BatchRunner
├── scan/                # BatchedScanner + ParallelRangeScanner + state
├── posts/               # PostBuilder (linked-list captions)
├── vault/               # AES-256-GCM chunked storage (uploader/downloader/crypto)
├── db/                  # SQLite schema (WAL + FTS5), stores, channel sync
├── models/              # Channel / Message / Media / Link dataclasses
├── config/              # config schema + loader
├── capabilities.py      # Tier 1 / Tier 2 detection
└── utils.py             # shared helpers (stable_channel_db_id, ...)
```

Guidelines:

- **CLI modules stay thin** — argument parsing and output formatting only.
  Business logic lives in `operations/`, `scan/`, `vault/`, `db/`.
- **No network calls in tests** — unit tests must run offline. Extract pure
  logic into testable functions (see `process_chunk` / `ChunkAssembler`).
- **Stable IDs only** — never use Python's built-in `hash()` for anything
  persisted. Use `tgkit.utils.stable_channel_db_id()`.
- **Secrets never enter the repo** — bot tokens, api hashes, session files
  and resume states are gitignored. Double-check before committing.
- **Respect Telegram's limits** — anything that can hit FloodWait must go
  through the throttle-aware pool and support resume.

## Testing

```bash
pytest tests/ -v                  # full suite
pytest tests/test_vault_fixes.py  # vault regression tests only
```

When fixing a bug, add a regression test that fails before the fix and
passes after it (see `tests/test_vault_fixes.py` for examples).

## Submitting changes

1. Fork / branch from `main`.
2. Make your change, with tests and a `CHANGELOG.md` entry.
3. Ensure `pytest tests/` passes and no secrets are included.
4. Open a Pull Request describing **what** changed and **why**.

## Reporting bugs

Open an issue with:

- tgkit version (`tgkit --version`) and Python version
- The exact command you ran (redact tokens/passwords!)
- Full traceback if available
- Whether the channel is public/private and the bot's role in it

## License

By contributing, you agree that your contributions are licensed under the
MIT License (see [LICENSE](LICENSE)).
