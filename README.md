# tgkit

**Unified Telegram toolkit for AI agents** — scan, copy, forward, vault, and manage Telegram channels through a single CLI.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python ≥3.10](https://img.shields.io/badge/python-%E2%89%A53.10-blue.svg)
![Version](https://img.shields.io/badge/version-0.2.1-green.svg)
![Tests](https://img.shields.io/badge/tests-56%20passed-brightgreen.svg)

[فارسی](#فارسی) | [English](#english)

---

## English

### What is tgkit?

tgkit is a CLI tool designed to be operated by **AI agents** (not humans). It provides a complete toolkit for working with Telegram channels:

- **Scan** channel history (batched + concurrent, 1000+ msg/s)
- **Copy** messages without "Forwarded from" header (via `file_id`)
- **Forward** messages (with header, native Telegram forward)
- **Reupload** files (download + re-upload, breaks copyright linkage)
- **Build posts** with linked-list captions (auto-split across multiple messages)
- **Vault** — encrypted chunked storage (AES-256-GCM, 2 GB files)
- **Database** — every operation logged to SQLite, synced to a Telegram channel

### Two-Tier Design

| Tier | Requirements | Capabilities |
|------|-------------|--------------|
| **Tier 1 — Basic** | 1 bot token | Send, forward, copy (no metadata), edit, delete, DB sync |
| **Tier 2 — Extended** | bot token + api_id + api_hash | Everything in Tier 1 + scan, copy via file_id, large files (2 GB), vault |

### Quick Start

```bash
# Install
pip install -e .

# Initialize
tgkit init
# Edit ~/.tgkit/config.json — fill in api_id and api_hash

# Add bots
tgkit bot add 8866132706:AAH_xxx...
tgkit bot add 8857297766:AAGJxxx...

# Verify
tgkit status
tgkit bot test

# Register channels
tgkit channel add @yxafile --role source
tgkit channel add -1003873843444 --role destination
tgkit channel add -1003873843444 --role db_sync

# Scan a channel
tgkit scan run https://t.me/yxafile/43966 --output scan.json

# Copy from scan results (killer feature)
tgkit copy --from-scan 1 --filter "ext=.zip"

# Build a post with linked-list captions
tgkit post create --from-scan 1 --header "📚 Manga Collection"

# Vault upload (encrypted, chunked)
tgkit vault upload large_file.zip --encrypt --password "secret"

# Resume an interrupted vault upload (reuses the original encryption salt)
tgkit vault upload large_file.zip --encrypt --resume

# Resume an interrupted vault download
tgkit vault download https://t.me/c/123/456 --resume
```

### Commands

| Command | Description |
|---------|-------------|
| `tgkit init` | Create config file |
| `tgkit status` | Show capabilities + config + DB state |
| `tgkit stats` | Show overall statistics |
| `tgkit bot add/list/remove/test` | Bot management |
| `tgkit channel add/list/remove` | Channel management |
| `tgkit scan run/resume/status` | Scan channel history |
| `tgkit copy/forward/reupload` | Copy (no header), forward (header), reupload (break copyright) |
| `tgkit post create` | Build linked-list posts with many links |
| `tgkit edit/delete` | Edit/delete messages in-place |
| `tgkit tag add/remove/list/search` | Tag management |
| `tgkit library list/search/info/stats` | Library queries |
| `tgkit log` | Query operations log |
| `tgkit db sync/download/find/status` | Database sync to Telegram channel |
| `tgkit vault upload/download/info` | Encrypted chunked storage |
| `tgkit migrate` | Import from legacy tg-vault |
| `tgkit shell` | Interactive REPL |

### Key Features

- **`--from-scan`**: Pull messages from DB by scan_id + filter — no manual link lists
- **`--continue` mode**: Scan results are uploaded to a channel every N messages (configurable). If interrupted, resume with `--continue-link` — the tool downloads the checkpoint, finds where it stopped, and continues.
- **Vault resume**: Interrupted vault uploads/downloads can be resumed (`--resume`). Resume state includes the encryption salt, so resuming an ENCRYPTED upload reuses the original key instead of corrupting the file (a bug the predecessor tg-vault had).
- **Bounded-memory downloads**: Chunks are streamed to disk in order as they arrive — a 2 GB vault file no longer needs 2 GB of RAM.
- **Stable channel IDs**: All DB channel IDs use SHA256-based `stable_channel_db_id()` — safe across processes (Python's `hash()` is randomized per run).
- **Linked-list posts**: Auto-split 500+ links across reply-chained messages
- **Atomic DB sync**: `editMessageMedia` replaces DB file in-place (msg_id preserved)
- **Adaptive throttle**: Per-bot backoff on FloodWait, decay on success
- **5-bot parallel**: Round-robin + per-channel cooldown
- **Resume**: All batch operations support resume from where they stopped

### Architecture

```
commands/       CLI (argparse) — no GUI, no interactive menu
transport/      Dual-mode: BotAPIClient (HTTP) + TgClient (pyrofork MTProto)
operations/     CopyOp, ForwardOp, ReuploadOp + BatchRunner
scan/           BatchedScanner (50 IDs/RPC, concurrent)
posts/          PostBuilder (linked-list captions)
vault/          AES-256-GCM + TGV1 header + chunked upload/download
db/             SQLite (WAL) + Store (CRUD) + Sync (Telegram channel)
capabilities.py Tier detection (Tier 1 vs Tier 2)
```

### Requirements

- Python ≥ 3.10
- pyrofork (NOT vanilla pyrogram — required for 64-bit channel IDs)
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- api_id + api_hash (from [my.telegram.org](https://my.telegram.org)) for Tier 2

### Security

- Vault encryption: **AES-256-GCM** with **PBKDF2-HMAC-SHA512** (600,000
  iterations, OWASP 2025) and a per-file random salt. The key is never stored;
  the manifest stores only the salt and a domain-separated password
  verification hash (compared constant-time).
- Chunk tampering is detected via the GCM auth tag (SHA256 of the whole file
  is verified after reassembly).
- **Never commit** `config.json`, `*.session`, or `*.vault_resume.json` —
  they are gitignored; keep it that way. Prefer the `TGKIT_PASSWORD` env
  var over `--password` to keep secrets out of shell history.
- Set restrictive permissions on the config and sessions directory:
  `chmod 700 ~/.tgkit && chmod 600 ~/.tgkit/config.json ~/.tgkit/sessions/*`
- File metadata (name, size, description) is stored in plaintext in the
  manifest — rename sensitive files before uploading.

### Migrating from tg-vault

```bash
tgkit migrate   # imports bots/channels/DB from a legacy tg-vault install
```

The vault format is compatible: chunk pipeline (compress → encrypt → TGV1
header) and manifest crypto parameters are identical, and legacy headerless
tg-vault chunks are handled gracefully on download.

### License

MIT — see [LICENSE](LICENSE). Changes are tracked in [CHANGELOG.md](CHANGELOG.md).

---

## فارسی

### tgkit چیست؟

tgkit یک ابزار خط فرمان (CLI) است که برای **عملیات توسط هوش مصنوعی** طراحی شده است. این ابزار یک جعبه‌ابزار کامل برای کار با کانال‌های تلگرام ارائه می‌دهد:

- **اسکن** تاریخچه کانال (دسته‌ای + همزمان، 1000+ پیام در ثانیه)
- **کپی** پیام‌ها بدون هدر «Forwarded from» (از طریق `file_id`)
- **فوروارد** پیام‌ها (با هدر، فوروارد بومی تلگرام)
- **ری‌آپلود** فایل‌ها (دانلود + آپلود مجدد، شکستن پیوند کپی‌رایت)
- **ساخت پست** با کپشن‌های لیست پیوندی (تقسیم خودکار بین چندین پیام)
- **Vault** — ذخیره‌سازی chunked رمزنگاری‌شده (AES-256-GCM، فایل‌های ۲ گیگابایتی)
- **پایگاه داده** — هر عملیات در SQLite ثبت می‌شود و با کانال تلگرام همگام‌سازی می‌گردد

### طراحی دو سطحی

| سطح | نیازمندی‌ها | قابلیت‌ها |
|------|-------------|------------|
| **سطح ۱ — پایه** | 1 bot token | ارسال، فوروارد، کپی (بدون متادیتا)، ویرایش، حذف، همگام‌سازی DB |
| **سطح ۲ — پیشرفته** | bot token + api_id + api_hash | همه قابلیت‌های سطح ۱ + اسکن، کپی با file_id، فایل‌های بزرگ (۲ گیگابایت)، vault |

### شروع سریع

```bash
# نصب
pip install -e .

# راه‌اندازی
tgkit init
# فایل ~/.tgkit/config.json را ویرایش کنید — api_id و api_hash را پر کنید

# اضافه کردن ربات
tgkit bot add 8866132706:AAH_xxx...
tgkit bot add 8857297766:AAGJxxx...

# بررسی
tgkit status
tgkit bot test

# ثبت کانال‌ها
tgkit channel add @yxafile --role source
tgkit channel add -1003873843444 --role destination
tgkit channel add -1003873843444 --role db_sync

# اسکن یک کانال
tgkit scan run https://t.me/yxafile/43966 --output scan.json

# کپی از نتایج اسکن (قابلیت کلیدی)
tgkit copy --from-scan 1 --filter "ext=.zip"

# ساخت پست با کپشن لیست پیوندی
tgkit post create --from-scan 1 --header "📚 مجموعه مانگا"

# آپلود vault (رمزنگاری‌شده، chunked)
tgkit vault upload large_file.zip --encrypt --password "secret"

# ادامه آپلود قطع‌شده (همان salt قبلی استفاده می‌شود)
tgkit vault upload large_file.zip --encrypt --resume

# ادامه دانلود قطع‌شده
tgkit vault download https://t.me/c/123/456 --resume
```

### دستورات

| دستور | توضیح |
|---------|-------|
| `tgkit init` | ایجاد فایل کانفیگ |
| `tgkit status` | نمایش قابلیت‌ها + کانفیگ + وضعیت DB |
| `tgkit stats` | نمایش آمار کلی |
| `tgkit bot add/list/remove/test` | مدیریت ربات |
| `tgkit channel add/list/remove` | مدیریت کانال |
| `tgkit scan run/resume/status` | اسکن تاریخچه کانال |
| `tgkit copy/forward/reupload` | کپی (بدون هدر)، فوروارد (با هدر)، ری‌آپلود (شکستن کپی‌رایت) |
| `tgkit post create` | ساخت پست با لیست پیوندی |
| `tgkit edit/delete` | ویرایش/حذف پیام در‌جا |
| `tgkit tag add/remove/list/search` | مدیریت تگ |
| `tgkit library list/search/info/stats` | جستجوی کتابخانه |
| `tgkit log` | جستجوی لاگ عملیات |
| `tgkit db sync/download/find/status` | همگام‌سازی DB با کانال تلگرام |
| `tgkit vault upload/download/info` | ذخیره‌سازی رمزنگاری‌شده chunked |
| `tgkit migrate` | ایمپورت از tg-vault قدیمی |
| `tgkit shell` | REPL تعاملی |

### ویژگی‌های کلیدی

- **`--from-scan`**: گرفتن پیام‌ها از DB با scan_id + فیلتر — نیازی به لیست لینک دستی نیست
- **حالت `--continue`**: نتایج اسکن هر N پیام (قابل تنظیم) در کانال آپلود می‌شود. اگر قطع شد، با `--continue-link` ادامه دهید — ابزار چک‌پوینت را دانلود می‌کند و از جایی که مانده ادامه می‌دهد.
- **ادامه (Resume) در Vault**: آپلود/دانلود قطع‌شده با `--resume` ادامه می‌یابد. state شامل salt رمزنگاری است، بنابراین ادامه‌ی آپلود رمزنگاری‌شده از همان کلید قبلی استفاده می‌کند، نه اینکه فایل را خراب کند (باگی که نسخه قبلی tg-vault داشت).
- **دانلود با حافظه محدود**: chunkها به‌محض رسیدن به‌ترتیب روی دیسک نوشته می‌شوند — فایل ۲ گیگابایتی دیگر به ۲ گیگ رم نیاز ندارد.
- **شناسه پایدار کانال‌ها**: همه DB IDها با `stable_channel_db_id()` مبتنی بر SHA256 ساخته می‌شوند (تابع `hash()` پایتون در هر اجرا تغییر می‌کند).
- **پست‌های لیست پیوندی**: تقسیم خودکار 500+ لینک بین پیام‌های reply-chained
- **همگام‌سازی اتمیک DB**: `editMessageMedia` فایل DB را در‌جا جایگزین می‌کند (msg_id حفظ می‌شود)
- **تروتل تطبیقی**: backoff هر بات روی FloodWait، decay روی موفقیت
- **5 بات موازی**: round-robin + cooldown هر کانال
- **ادامه (Resume)**: همه عملیات‌های batch از جایی که متوقف شده‌اند ادامه می‌یابند

### معماری

```
commands/       CLI (argparse) — بدون GUI، بدون منوی تعاملی
transport/      دو حالته: BotAPIClient (HTTP) + TgClient (pyrofork MTProto)
operations/     CopyOp, ForwardOp, ReuploadOp + BatchRunner
scan/           BatchedScanner (50 آیدی/درخواست، همزمان)
posts/          PostBuilder (کپشن لیست پیوندی)
vault/          AES-256-GCM + هدر TGV1 + آپلود/دانلود chunked
db/             SQLite (WAL) + Store (CRUD) + Sync (کانال تلگرام)
capabilities.py تشخیص سطح (سطح ۱ vs سطح ۲)
```

### نیازمندی‌ها

- Python ≥ 3.10
- pyrofork (نه pyrogram معمولی — برای آیدی کانال‌های 64 بیتی ضروری است)
- یک bot token تلگرام (از [@BotFather](https://t.me/BotFather))
- api_id + api_hash (از [my.telegram.org](https://my.telegram.org)) برای سطح ۲

### لایسنس

MIT
