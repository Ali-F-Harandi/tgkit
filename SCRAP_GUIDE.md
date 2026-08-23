# tgkit Scraping Guide — Challenges & Solutions

> This guide is based on real-world experience using tgkit to scan Telegram channels. Every challenge listed here was encountered in practice, and the solutions provided have been tested and verified.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Installation & Setup](#installation--setup)
3. [Client-Side Challenges (Cannot Be Fixed in Code)](#client-side-challenges-cannot-be-fixed-in-code)
4. [Solvable Challenges & Solutions](#solvable-challenges--solutions)
5. [Best Practices](#best-practices)
6. [Troubleshooting](#troubleshooting)
7. [FAQ](#faq)

---

## Quick Start

```bash
# Install
cd tgkit
pip install -e .

# Initialize
tgkit init
# Edit ~/.tgkit/config.json — fill in api_id, api_hash, bots

# Fast channel scan (recommended method)
tgkit scan run https://t.me/yxafile/43966 --parallel-ranges --output results.json

# If the scan is interrupted, just re-run the same command (auto-resume)
tgkit scan run https://t.me/yxafile/43966 --parallel-ranges --output results.json
```

---

## Installation & Setup

### Prerequisites

- Python ≥ 3.10
- `pyrofork` (NOT vanilla pyrogram — required for 64-bit channel IDs)
- A bot token from [@BotFather](https://t.me/BotFather)
- `api_id` + `api_hash` from [my.telegram.org](https://my.telegram.org)

### Installation

```bash
cd tgkit
pip install -e .
```

### Config Setup

```bash
tgkit init
```

Edit `~/.tgkit/config.json`:

```json
{
  "api": {
    "api_id": 12345,
    "api_hash": "your_api_hash_here",
    "session_dir": "~/.tgkit/sessions"
  },
  "bots": [
    {"token": "123456:ABC...", "username": "mybot1"},
    {"token": "234567:DEF...", "username": "mybot2"}
  ],
  "channels": {
    "default_destination": -1001234567890,
    "db_sync": -1001234567890
  }
}
```

### Adding Bots & Channels

```bash
# Add a bot (auto-validates via getMe)
tgkit bot add 123456:ABC-DEF...

# Register a destination channel
tgkit channel add -1001234567890 --role destination

# Check status
tgkit status
tgkit bot test
```

---

## Client-Side Challenges (Cannot Be Fixed in Code)

These challenges are due to Telegram's inherent limitations. **No code can completely eliminate them** — we can only manage them.

### 1. `channels.GetMessages` Rate Limit

**Problem:** Telegram enforces strict rate limits on `channels.GetMessages`. Contrary to popular belief, the "30 requests per second" figure applies only to **broadcasting** (sending messages), not to getMessages.

**Evidence:**
- levlam (Telegram maintainer) states in [tdlib/td#2633](https://github.com/tdlib/td/issues/2633): *"it is impossible to scale constant polling of data. There is no way."*
- In practice, every ~500 messages (5 batches of 100), a 30-second FloodWait is received.

**Management:**
- tgkit automatically handles FloodWaits ≤60 seconds via `sleep_threshold=60`
- Parallel range scanning distributes this limit across N bots → N× total throughput

### 2. Per-Channel Limit (Independent of Bot)

**Problem:** FloodWait is keyed on `(method, input_parameters)`. Since the channel peer is an input parameter, **the channel itself gets rate-limited**, not just the bot.

**Evidence:**
- When 5 bots work on the same channel with a shared pool, severe FloodWait occurs
- When 5 bots work on disjoint slices of the same channel, FloodWait is reduced

**Management:**
- Use `--parallel-ranges` (each bot gets a dedicated slice)
- NEVER use multiple bots on a shared pool for the same channel

### 3. `auth.importBotAuthorization` Limit

**Problem:** This method is extremely sensitive. Repeated auth attempts cause 30+ minute FloodWaits.

**Common Causes:**
- Deleting the session file on every run
- Restart loop (crash → restart → re-auth → longer FloodWait → crash)
- Starting multiple bots simultaneously with `asyncio.gather` (5 auth attempts in one second)

**Management:**
- **NEVER delete session files** — keep them in `~/.tgkit/sessions/`
- tgkit automatically performs staggered startup (2-second intervals)
- If a bot gets auth FloodWait, wait it out (do NOT restart)

### 4. Session File Concurrency Limit

**Problem:** pyrofork creates a SQLite session file per bot. Running multiple processes with the same session file causes `database is locked` errors.

**Management:**
- Run only one tgkit process at a time
- If you need multiple processes, use different `session_dir` for each

### 5. Network Timeout Limit

**Problem:** If the network is slow or Telegram is unresponsive, RPCs may time out.

**Management:**
- tgkit automatically retries (4 times with exponential backoff)
- For slow networks, increase `inter_batch_sleep`

---

## Solvable Challenges & Solutions

### 1. Slow Scanning with One Bot

**Problem:** With 1 bot, speed is only ~12 msg/s (due to constant FloodWaits).

**Solution:** Use `--parallel-ranges` with multiple bots:

```bash
tgkit scan run https://t.me/channel/10000 --parallel-ranges --output results.json
```

With 5 bots, speed reaches ~766 msg/s (64× faster!).

### 2. Scan Gets Interrupted (timeout, crash, etc.)

**Problem:** Long scans may be interrupted due to environment timeouts or crashes.

**Solution:** tgkit automatically resumes. Just re-run the same command:

```bash
# First run (gets interrupted)
tgkit scan run https://t.me/channel/10000 --parallel-ranges --output results.json

# Resume (same command)
tgkit scan run https://t.me/channel/10000 --parallel-ranges --output results.json
```

tgkit detects that the previous scan was interrupted and resumes from where it left off. Bots that completed their slice are skipped.

### 3. "Scan already completed" Message

**Problem:** When a scan is complete, re-running shows "already completed".

**Solution:** To scan again, use `--fresh`:

```bash
tgkit scan run https://t.me/channel/10000 --parallel-ranges --fresh --output results.json
```

### 4. Bot Gets Auth FloodWait

**Problem:** A bot gets a 30+ minute auth FloodWait.

**Causes:**
- Session file was deleted
- Restart loop
- Multiple processes using the same session

**Solution:**
1. Wait for the FloodWait to expire (can be 30+ minutes)
2. Do NOT delete session files
3. Use only healthy bots (test with `tgkit bot test`)
4. If a bot persistently fails, temporarily remove it from config

### 5. SQLite "database is locked"

**Problem:** `sqlite3.OperationalError: database is locked`

**Cause:** Multiple processes writing to the DB simultaneously.

**Solution:**
- Run only one tgkit process
- WAL mode is enabled by default (for better concurrency)
- `PRAGMA busy_timeout=30000` is set

### 6. Different Speeds for Different Bots

**Problem:** Some bots finish their slice faster than others.

**Cause:** Different bots have different FloodWait histories. Bots that recently got FloodWait are slower.

**Solution:** This is normal. tgkit automatically skips fast bots when they finish their slice.

### 7. Deleted Messages

**Problem:** Some messages are counted as "deleted".

**Cause:** Messages were actually deleted from the channel, or the bot doesn't have access.

**Solution:** This is normal. In the JSON output, these messages are not included (only existing messages are saved).

### 8. Non-Deterministic `hash()` Issue

**Problem:** Original tgkit used `hash(str(channel_id))` for channel_db_id. But Python's `hash()` produces different values per process (PYTHONHASHSEED).

**Solution:** In the modified version, `stable_channel_db_id()` is used, which is SHA256-based:

```python
from tgkit.scan.parallel_scanner import stable_channel_db_id
channel_db_id = stable_channel_db_id("@yxafile")  # Always the same value
```

---

## Best Practices

### 1. Bot Configuration

- Use **at least 3 bots** for large scans (>10K messages)
- Use **at most 5-10 bots** — more than this provides no benefit (per-channel limit)
- Create bots from [@BotFather](https://t.me/BotFather)
- Add each bot to the target channels (if the channel is private)

### 2. Session Management

```bash
# DO NOT delete session files!
# If corrupted, delete once and re-run
rm ~/.tgkit/sessions/bot_*.session
tgkit bot test  # recreates sessions
```

### 3. Scanning Large Channels

For large channels (>50K messages):

```bash
# With parallel-ranges
tgkit scan run https://t.me/channel/50000 --parallel-ranges --output results.json

# If interrupted, resume
tgkit scan run https://t.me/channel/50000 --parallel-ranges --output results.json
```

### 4. Parameter Tuning

```bash
# Smaller batch size for slow networks
tgkit scan run <link> --parallel-ranges --batch-size 50 --inter-batch-sleep 0.5

# Larger batch size for fast networks (not more than 100)
tgkit scan run <link> --parallel-ranges --batch-size 100 --inter-batch-sleep 0.2
```

### 5. Progress Monitoring

tgkit prints progress every 15 seconds. For continuous monitoring:

```bash
# In a separate terminal
tail -f /tmp/tgkit_scan.log

# Or use the status command
tgkit scan status
```

---

## Troubleshooting

### Issue: "ModuleNotFoundError: No module named 'pyrogram'"

**Solution:** Install pyrofork:

```bash
pip install pyrofork tgcrypto
```

### Issue: "Config not found at: ~/.tgkit/config.json"

**Solution:** Run `tgkit init` first.

### Issue: "Scanning requires Tier 2 (api_id + api_hash)"

**Solution:** Set `api_id` and `api_hash` in config.json (from [my.telegram.org](https://my.telegram.org)).

### Issue: Constant FloodWait even with parallel-ranges

**Solution:**
1. Increase `inter_batch_sleep` (`--inter-batch-sleep 0.5`)
2. Reduce the number of bots (the channel may be too rate-limited)
3. Wait (previous FloodWaits need to expire)

### Issue: "database is locked"

**Solution:**
1. Ensure only one tgkit process is running
2. If a process was killed, the DB lock may persist — wait or `rm ~/.tgkit/tgkit.db-wal`

### Issue: Bot Doesn't Have Channel Access

**Solution:**
- For public channels: no need to add the bot
- For private channels: add the bot to the channel
- For restricted channels: the bot must be an admin

---

## FAQ

### Q: How many bots is best?

**A:** 3-5 bots is optimal for most cases. More than 5 bots provides no benefit because the per-channel limit becomes the bottleneck.

### Q: Why is my speed lower than 766 msg/s?

**A:** Speed depends on:
- Number of healthy bots
- Bots' FloodWait history
- Network speed
- Channel size (larger channels may get rate-limited)

### Q: Can I scan multiple channels simultaneously?

**A:** Yes, but run each channel in a separate terminal. tgkit scans one channel per execution.

```bash
# Terminal 1
tgkit scan run https://t.me/channel1/1000 --parallel-ranges --output ch1.json

# Terminal 2 (with different bots)
tgkit scan run https://t.me/channel2/2000 --parallel-ranges --output ch2.json
```

### Q: Can I use a user account instead of a bot?

**A:** tgkit only supports bot tokens. User accounts risk being banned and are not recommended.

### Q: How to process the JSON output?

**A:** The output is a list of dicts:

```python
import json

with open('results.json', 'r', encoding='utf-8') as f:
    messages = json.load(f)

# Filter by extension
zip_files = [m for m in messages if m.get('file_extension') == '.zip']

# Filter by size
large_files = [m for m in messages if (m.get('file_size') or 0) > 10 * 1024 * 1024]

# Group by media_type
from collections import Counter
types = Counter(m.get('media_type') for m in messages)
print(types)
```

### Q: How does resume work?

**A:** tgkit stores a record per bot in the `bot_scan_state` table:
- `last_completed_id`: last processed ID
- `found_count` / `deleted_count`: number of found/deleted messages
- `status`: pending | running | completed | failed

When you re-run:
- `completed` bots → skipped
- `running`/`interrupted` bots → resume from `last_completed_id - 1`
- `pending` bots → start fresh

### Q: How to reset state?

**A:** To start completely fresh:

```bash
# Clear scan state
python3 -c "
import sqlite3, os
db_path = os.path.expanduser('~/.tgkit/tgkit.db')
conn = sqlite3.connect(db_path)
c = conn.cursor()
c.execute('DELETE FROM messages')
c.execute('DELETE FROM scans')
c.execute('DELETE FROM bot_scan_state')
conn.commit()
"

# Or with --fresh flag
tgkit scan run <link> --parallel-ranges --fresh
```

---

## Summary

| Challenge | Type | Solution |
|-----------|------|----------|
| getMessages rate limit | Client-side | parallel-ranges + multiple bots |
| Per-channel limit | Client-side | parallel-ranges (not shared pool) |
| Auth FloodWait | Client-side | persisted sessions + staggered startup |
| Scan interruption | Solvable | auto-resume |
| Non-deterministic hash() | Solvable | stable_channel_db_id (SHA256) |
| SQLite lock | Solvable | WAL mode + db_lock |
| Slow speed | Solvable | parallel-ranges + parameter tuning |

---

## Resources

- [FloodWait Research Report](FLOODWAIT_RESEARCH_REPORT.md) — 40-page report with citations
- [tgkit README](README.md) — main tgkit documentation
- [pyrofork docs](https://telegramplayground.github.io/pyrogram/) — pyrofork documentation
- [Telegram API errors](https://core.telegram.org/api/errors) — official Telegram documentation

---

## Support

If you find a bug or have a suggestion, please open an issue.
