# tgkit — The Plain-English Guide

This guide is for **everyone** — no Telegram API knowledge required. If you
can copy-paste commands into a terminal, you can use tgkit.

[فارسی](guide-fa.md) | **English**

---

## What is tgkit, in one paragraph?

tgkit is a command-line tool that moves files between your computer and
Telegram channels: **download** a file from a channel link, **upload** a
file to your channel, **copy** messages between channels, **scan** a whole
channel's history into a searchable database. It works through bot accounts
you create yourself, so it needs no human login and nothing is shared with
anyone else's servers.

## Why not just use the Telegram app?

Three words: **file size limits**. The "easy" Telegram API that most tools
use secretly caps downloads at **20 MB** and uploads at **50 MB**. tgkit
uses the same internal protocol the real Telegram clients use (MTProto),
so it handles files up to **2 GB** — and unlimited total size when you use
the vault. Everything below handles this automatically; you don't need to
think about it.

| What you want to do | Limit with "easy" tools | Limit with tgkit |
|---|---|---|
| Download a file | 20 MB | **2 GB** |
| Upload a file | 50 MB | **2 GB** (or unlimited via vault) |
| Download 500 files overnight | manual clicking | one command |

---

## Part 1 — Setup (10 minutes, once)

### Step 1: Install Python and tgkit

You need Python 3.10 or newer. Check with:

```bash
python3 --version
```

Then install tgkit:

```bash
git clone https://github.com/Ali-F-Harandi/tgkit.git
cd tgkit
pip install -e .
```

Check it works:

```bash
tgkit --version
```

### Step 2: Get your Telegram API keys

These two codes (api_id and api_hash) tell Telegram that *you* — not a
random stranger — are connecting.

1. Open https://my.telegram.org in a browser and log in with your phone
   number (Telegram sends a code in the app).
2. Click **API Development Tools**.
3. Fill the form: any app title (e.g. "my toolkit"), any short name,
   platform can stay "Other".
4. After submitting you'll see **App api_id** (a number like `21724`) and
   **App api_hash** (a long string like `3e0cb5ef...`). Save both.

### Step 3: Create a bot

Bots do the actual work. One bot is enough to start.

1. In Telegram, open a chat with **@BotFather** (it has a blue check).
2. Send `/newbot`, choose a display name, then a username ending in `bot`
   (e.g. `myfiles_bot`).
3. BotFather replies with a **token** like
   `8866132706:AAGSmU4xNBLgoqi9ceYapHi-PEB57MvDm3I`. Save it.

### Step 4: Connect everything

```bash
# Create the config file
tgkit init

# Tell tgkit your bot
tgkit bot add 8866132706:AAGSmU4xNBLgoqi9ceYapHi-PEB57MvDm3I
```

Now open the config file (it prints the path — usually `~/.tgkit/config.json`)
in any text editor and fill in the two fields from Step 2:

```json
"api": {
    "api_id": 21724,
    "api_hash": "3e0cb5efcd52300aec5994fdfc5bdc16",
    ...
}
```

### Step 5: The magic health check

```bash
tgkit doctor --deep
```

This checks everything: Python version, installed packages, config file,
your keys, every bot token (live), internet connection, disk space, and
even does a real test login. If anything is wrong it tells you **exactly
what to type to fix it**. When it prints `✓ All checks passed`, you're done.

---

## Part 2 — Downloading files

You have a Telegram message link like `https://t.me/c/3786156476/46839`
(right-click any channel message → Copy Message Link).

```bash
# One file
tgkit fetch https://t.me/c/3786156476/46839

# Several files, order kept
tgkit fetch https://t.me/c/3786156476/46815 https://t.me/c/3786156476/46813

# Into a specific folder
tgkit fetch https://t.me/c/3786156476/46839 --out ~/Downloads
```

That's the whole command. Files land in your current folder (or `--out`),
existing files are never overwritten (tgkit auto-renames to `file (1).zip`).

**What if the file is huge?** Doesn't matter. A 122 MB file, a 900 MB
video — `fetch` uses MTProto automatically and shows a live progress bar.
If you point it at a vault package (a file that was split into chunks),
it notices and switches to vault mode by itself.

**The bot must be able to see the file.** For private channels, add your
bot as a member/admin of that channel first — a bot can only download
from channels it is in.

## Part 3 — Uploading files

```bash
# Upload to a channel (use its @username or -100 ID)
tgkit send report.zip --to @mychannel

# Several files with a caption
tgkit send a.zip b.zip --to @mychannel --caption "Project backup"

# Big file? Same command. 800 MB works fine.
tgkit send big_video.mkv --to @mychannel
```

You get back a shareable `t.me` link for every uploaded file.

Requirements for the destination channel: your bot must be an **admin**
with the *Post messages* permission. If it isn't, tgkit tells you that's
the problem instead of failing silently.

**Files over 2 GB** can't exist as a single Telegram message — for those,
use the vault, which splits any file into 19 MB encrypted chunks with no
total limit:

```bash
tgkit vault upload huge_dataset.zip --to @mychannel --encrypt
tgkit vault download <the share link tgkit prints>
```

## Part 4 — Other useful things

| Command | What it does |
|---|---|
| `tgkit doctor` | Health-check everything, print fixes |
| `tgkit status` | Show config, bots, capabilities |
| `tgkit scan run <link>` | Read a whole channel's history into a database |
| `tgkit copy <link> --to @chan` | Copy messages without the "Forwarded from" header |
| `tgkit forward <link> --to @chan` | Native forward (keeps the header) |
| `tgkit vault upload/download` | Encrypted chunked storage for huge files |

Run `tgkit --help` (or `tgkit <command> --help`) to see everything.

---

## Troubleshooting

**`Config not found`** — run `tgkit init`, then `tgkit bot add <TOKEN>`.

**`file is too big` / downloads break on big files** — that's the old 20 MB
Bot API trap. Use `tgkit fetch` (it always uses MTProto). `tgkit doctor`
confirms your api_id/api_hash are set — those keys are what unlock big
files.

**`bot is not an admin` / uploads fail** — add the bot as admin to the
destination channel: channel settings → Administrators → Add admin.

**`ApiIdInvalid` / MTProto won't start** — your api_id/api_hash are wrong.
Get fresh ones at https://my.telegram.org and update the config.

**Stale login errors** — delete `~/.tgkit/sessions/*` and re-run; bots
re-login from their tokens automatically.

**Anything else** — run `tgkit doctor --deep` first. It diagnoses the 95%
case and prints the exact fix.
