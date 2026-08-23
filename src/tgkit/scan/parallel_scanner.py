"""ParallelRangeScanner — scans one channel with N bots on N disjoint ID ranges.

DESIGN (proven in benchmarks — see SCRAP_GUIDE.md):
    - Each bot gets a dedicated, contiguous ID range (NO round-robin sharing)
    - Each bot only ever queries ONE channel → per-(bot,channel) flood bucket
      is the only bucket that matters, and it's the smallest possible
    - Bots run concurrently via asyncio.gather → FloodWaits overlap in wall-clock
    - Per-bot resume state in bot_scan_state table → re-running the same scan
      resumes from where each bot left off, skipping finished bots entirely

WHY THIS IS FAST (vs. BatchedScanner's shared-pool approach):
    BatchedScanner:  5 bots × 1 channel = each bot hits the same channel
                     → per-channel flood bucket gets 5× the pressure
                     → triggers 30s FloodWait every ~500 msgs per bot
    ParallelRangeScanner: 5 bots × 1 channel = each bot hits a disjoint
                           ID range on the same channel; the channel peer
                           is still shared, but the *load pattern* (each bot
                           doing 1/5 of the work) means each bot's flood
                           bucket fills 5× slower → effectively 5× throughput

BENCHMARKS (channel @yxafile, 20K IDs):
    BatchedScanner (shared pool, 5 bots):  ~12 msg/s aggregate (FloodWait-bound)
    ParallelRangeScanner (5 bots, ranges): ~766 msg/s aggregate (5×150/bot)

RESUME / CONTINUE:
    - bot_scan_state table tracks per-bot: last_completed_id, found, deleted, status
    - Re-running `tgkit scan run <link> --parallel-ranges` on the same scan:
        * Bots with status='completed' are skipped entirely
        * Bots with status='running'/'interrupted' resume from last_completed_id - 1
        * Bots with status='pending' start fresh
    - The scan_id is reused if the prior scan on this channel is not 'completed'

FAULT TOLERANCE:
    - Each bot retries FloodWait up to max_floodwait_retries times
    - After exhausting retries, the bot's slice is marked 'failed' but the scan
      continues with the remaining bots
    - Re-running the scan will retry failed slices from where they left off
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pyrogram.errors import FloodWait

from tgkit.config.schema import Config
from tgkit.transport.bot_pool import AsyncBotPool
from tgkit.transport.bot import Bot
from tgkit.scan.extract import extract_message_info
from tgkit.scan.state import ScanState
from tgkit.models.message import MessageInfo

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Config + result types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ParallelScanPolicy:
    """Tuning knobs for ParallelRangeScanner.

    Defaults are based on the research report and real-world benchmarks.
    These values balance throughput vs. FloodWait risk conservatively.
    """
    batch_size: int = 100           # IDs per get_messages RPC (Telegram max for channels)
    replies: int = 0                # 0 = skip reply chain (halves server work)
    inter_batch_sleep: float = 0.3  # small gap between batches — avoids FloodWait
    fw_margin_s: float = 2.0        # extra sleep after a FloodWait (in addition to e.value)
    save_every_n_batches: int = 3   # persist resume state every N batches
    max_floodwait_retries: int = 8  # max FloodWait retries per batch before giving up
    max_consecutive_errors: int = 5 # max non-FloodWait errors before marking slice failed


@dataclass
class BotSliceResult:
    """Result of one bot's slice scan."""
    bot_idx: int
    username: str
    range_start: int
    range_end: int
    found: int = 0
    deleted: int = 0
    elapsed_seconds: float = 0.0
    rate: float = 0.0
    resumed: bool = False           # True if this bot resumed from a saved state
    error: str | None = None
    floodwait_count: int = 0        # total FloodWaits encountered by this bot


@dataclass
class ParallelScanResult:
    """Aggregate result of a parallel-range scan."""
    scan_id: int
    slices: list[BotSliceResult] = field(default_factory=list)
    total_found: int = 0
    total_deleted: int = 0
    elapsed_seconds: float = 0.0
    aggregate_rate: float = 0.0
    all_completed: bool = False
    failed_bots: list[int] = field(default_factory=list)  # bot_idx list


# ─────────────────────────────────────────────────────────────────────────────
# Range splitting
# ─────────────────────────────────────────────────────────────────────────────


def split_range(start_id: int, end_id: int, n_bots: int) -> list[tuple[int, int]]:
    """Split [start_id, end_id] into n_bots contiguous, disjoint, equal sub-ranges.

    Returns a list of (range_start, range_end) tuples where:
        - range_start >= range_end  (we scan backwards)
        - the union of all ranges == [start_id, end_id]
        - ranges are contiguous (no gaps, no overlaps)

    Example: split_range(20000, 1, 5) ->
        [(20000, 16001), (16000, 12001), (12000, 8001), (8000, 4001), (4000, 1)]
    """
    if n_bots < 1:
        raise ValueError(f"n_bots must be >= 1, got {n_bots}")
    if start_id < end_id:
        raise ValueError(f"start_id ({start_id}) must be >= end_id ({end_id})")

    total = start_id - end_id + 1
    base = total // n_bots
    remainder = total % n_bots

    slices: list[tuple[int, int]] = []
    cursor = start_id
    for i in range(n_bots):
        # distribute the remainder across the first `remainder` bots
        chunk = base + (1 if i < remainder else 0)
        range_start = cursor
        range_end = cursor - chunk + 1
        if range_end < end_id:
            range_end = end_id
        slices.append((range_start, range_end))
        cursor = range_end - 1
        if cursor < end_id:
            break

    return slices


# stable_channel_db_id moved to tgkit.utils (re-exported for compat)
from tgkit.utils import stable_channel_db_id  # noqa: E402,F401


# ─────────────────────────────────────────────────────────────────────────────
# ParallelRangeScanner
# ─────────────────────────────────────────────────────────────────────────────


class ParallelRangeScanner:
    """Scan one channel with N bots on N disjoint ID ranges.

    Usage:
        scanner = ParallelRangeScanner(pool, config, db_store, policy)
        result = await scanner.scan(
            channel="@yxafile",
            start_id=43966,
            end_id=1,
            scan_id=42,
            on_progress=my_progress_cb,
            on_batch=my_batch_cb,
        )
    """

    def __init__(
        self,
        pool: AsyncBotPool,
        config: Config,
        db_store: Any,                # tgkit.db.store.Store
        policy: ParallelScanPolicy | None = None,
    ):
        self.pool = pool
        self.config = config
        self.store = db_store
        self.policy = policy or ParallelScanPolicy()

    async def scan(
        self,
        channel: str | int,
        start_id: int,
        end_id: int,
        scan_id: int,
        on_progress: Callable[[dict[int, dict]], None] | None = None,
        on_batch: Callable[[list[MessageInfo]], None] | None = None,
    ) -> ParallelScanResult:
        """Scan [start_id, end_id] backwards using all bots in the pool in parallel.

        Each bot gets a dedicated ID slice. Per-bot resume state is read from
        and written to bot_scan_state. Bots that already finished are skipped.

        Args:
            channel: Channel @username or numeric ID
            start_id: Highest message ID to scan
            end_id: Lowest message ID to scan (usually 1)
            scan_id: DB scan ID (for resume state + message storage)
            on_progress: callback called periodically with {bot_idx: {found, deleted, ...}}
            on_batch: callback called for each batch of messages (for DB insert)

        Returns:
            ParallelScanResult with aggregate + per-bot stats
        """
        n_bots = self.pool.size
        slices = split_range(start_id, end_id, n_bots)

        # Make sure resume rows exist for this scan
        assignments = [
            {
                "bot_idx": i,
                "bot_username": self.pool.bots[i].username or f"bot{i}",
                "range_start": slices[i][0],
                "range_end": slices[i][1],
            }
            for i in range(n_bots)
        ]
        # init_for_scan uses INSERT OR IGNORE, so it's safe to call on every run
        self.store.bot_scan_state.init_for_scan(scan_id, assignments)

        logger.info(
            f"ParallelRangeScanner: scan #{scan_id} on {channel}, "
            f"{start_id - end_id + 1} IDs across {n_bots} bots, "
            f"slices={slices}"
        )

        # Per-bot progress dict (for the on_progress callback)
        progress: dict[int, dict] = {
            i: {
                "bot_idx": i,
                "username": assignments[i]["bot_username"],
                "range_start": slices[i][0],
                "range_end": slices[i][1],
                "found": 0,
                "deleted": 0,
                "last_id": slices[i][0],
                "status": "running",
                "started_at": time.time(),
                "floodwaits": 0,
            }
            for i in range(n_bots)
        }

        # Shared DB write lock (sqlite3 connections aren't fully async-safe)
        db_lock = asyncio.Lock()
        channel_db_id = stable_channel_db_id(channel)

        # Run all bots concurrently
        tasks = [
            self._scan_one_slice(
                bot_idx=i,
                bot=self.pool.bots[i],
                channel=channel,
                channel_db_id=channel_db_id,
                scan_id=scan_id,
                range_start=slices[i][0],
                range_end=slices[i][1],
                progress=progress,
                db_lock=db_lock,
                on_batch=on_batch,
                on_progress=on_progress,
            )
            for i in range(n_bots)
        ]

        t0 = time.time()
        slice_results = await asyncio.gather(*tasks)
        elapsed = time.time() - t0

        # Aggregate
        total_found = sum(r.found for r in slice_results)
        total_deleted = sum(r.deleted for r in slice_results)
        failed_bots = [r.bot_idx for r in slice_results if r.error is not None]
        all_done = len(failed_bots) == 0

        # Update parent scan row
        if all_done:
            self.store.scans.complete(
                scan_id=scan_id,
                found_count=total_found,
                deleted_count=total_deleted,
            )
        else:
            self.store.scans.mark_interrupted(scan_id)

        return ParallelScanResult(
            scan_id=scan_id,
            slices=slice_results,
            total_found=total_found,
            total_deleted=total_deleted,
            elapsed_seconds=elapsed,
            aggregate_rate=(total_found + total_deleted) / elapsed if elapsed > 0 else 0,
            all_completed=all_done,
            failed_bots=failed_bots,
        )

    async def _scan_one_slice(
        self,
        bot_idx: int,
        bot: Bot,
        channel: str | int,
        channel_db_id: int,
        scan_id: int,
        range_start: int,
        range_end: int,
        progress: dict[int, dict],
        db_lock: asyncio.Lock,
        on_batch: Callable[[list[MessageInfo]], None] | None,
        on_progress: Callable[[dict[int, dict]], None] | None,
    ) -> BotSliceResult:
        """Scan one bot's slice [range_start, range_end] backwards.

        Resumes from bot_scan_state if a prior run left off mid-slice.
        Marks the slice completed in bot_scan_state when done.
        On persistent errors, marks the slice as 'failed' (scan continues).
        """
        # Read resume state
        resume = self.store.bot_scan_state.get(scan_id, bot_idx)
        username = (bot.username or f"bot{bot_idx}")

        if resume and resume["status"] == "completed":
            logger.info(f"bot{bot_idx} @{username}: slice already completed — skipping")
            progress[bot_idx].update({
                "found": resume["found_count"],
                "deleted": resume["deleted_count"],
                "last_id": range_end,
                "status": "completed",
                "elapsed": 0,
            })
            if on_progress:
                on_progress(progress)
            return BotSliceResult(
                bot_idx=bot_idx, username=username,
                range_start=range_start, range_end=range_end,
                found=resume["found_count"], deleted=resume["deleted_count"],
                resumed=True,
            )

        # Determine start point (resume or fresh)
        if resume and resume["last_completed_id"] and resume["last_completed_id"] <= range_start:
            current_id = resume["last_completed_id"] - 1
            found_count = resume["found_count"]
            deleted_count = resume["deleted_count"]
            logger.info(
                f"bot{bot_idx} @{username}: RESUMING from id={current_id} "
                f"(found={found_count}, deleted={deleted_count})"
            )
            resumed = True
        else:
            current_id = range_start
            found_count = 0
            deleted_count = 0
            resumed = False

        # Mark as running
        self.store.bot_scan_state.update_progress(
            scan_id, bot_idx, current_id + 1, found_count, deleted_count, "running",
        )

        t0 = time.time()
        batch_count = 0
        batch_size = self.policy.batch_size
        consecutive_errors = 0
        total_floodwaits = 0

        while current_id >= range_end:
            batch_end = max(range_end, current_id - batch_size + 1)
            ids = list(range(current_id, batch_end - 1, -1))

            # Fetch with FloodWait retry
            msgs, fw_count, err = await self._fetch_with_retry(bot, channel, ids)
            total_floodwaits += fw_count

            if err is not None:
                consecutive_errors += 1
                logger.error(
                    f"bot{bot_idx} @{username}: batch {batch_count} failed "
                    f"(consecutive_errors={consecutive_errors}): {err}"
                )
                if consecutive_errors >= self.policy.max_consecutive_errors:
                    # Mark slice as failed, but persist progress for resume
                    async with db_lock:
                        self.store.bot_scan_state.update_progress(
                            scan_id, bot_idx, current_id + 1,
                            found_count, deleted_count, "failed",
                        )
                    progress[bot_idx].update({
                        "found": found_count,
                        "deleted": deleted_count,
                        "last_id": current_id,
                        "status": "failed",
                        "error": str(err),
                    })
                    if on_progress:
                        on_progress(progress)
                    return BotSliceResult(
                        bot_idx=bot_idx, username=username,
                        range_start=range_start, range_end=range_end,
                        found=found_count, deleted=deleted_count,
                        elapsed_seconds=time.time() - t0,
                        rate=0, resumed=resumed,
                        error=str(err), floodwait_count=total_floodwaits,
                    )
                # Brief backoff before retrying the same batch
                await asyncio.sleep(2 ** min(consecutive_errors, 5))
                continue

            # Reset error counter on success
            consecutive_errors = 0

            # Extract
            batch_messages: list[MessageInfo] = []
            for msg in msgs:
                if msg is None or getattr(msg, "empty", False):
                    deleted_count += 1
                    continue
                info = extract_message_info(msg)
                if info:
                    batch_messages.append(info)
                    found_count += 1

            current_id = batch_end - 1
            batch_count += 1

            # on_batch callback (for DB insert by the caller)
            if on_batch and batch_messages:
                async with db_lock:
                    on_batch(batch_messages)

            # Persist resume state every N batches
            if batch_count % self.policy.save_every_n_batches == 0:
                async with db_lock:
                    self.store.bot_scan_state.update_progress(
                        scan_id, bot_idx, current_id + 1,
                        found_count, deleted_count, "running",
                    )
                # Also update the in-memory progress dict
                progress[bot_idx].update({
                    "found": found_count,
                    "deleted": deleted_count,
                    "last_id": current_id,
                    "floodwaits": total_floodwaits,
                })
                if on_progress:
                    on_progress(progress)

            # Inter-batch sleep (proven to *raise* throughput by avoiding FloodWait)
            await asyncio.sleep(self.policy.inter_batch_sleep)

        elapsed = time.time() - t0
        total_done = found_count + deleted_count
        rate = total_done / elapsed if elapsed > 0 else 0

        # Mark slice completed
        async with db_lock:
            self.store.bot_scan_state.mark_completed(
                scan_id, bot_idx, found_count, deleted_count,
            )

        progress[bot_idx].update({
            "found": found_count,
            "deleted": deleted_count,
            "last_id": range_end,
            "status": "completed",
            "elapsed": elapsed,
            "rate": rate,
            "floodwaits": total_floodwaits,
        })
        if on_progress:
            on_progress(progress)

        logger.info(
            f"bot{bot_idx} @{username}: slice DONE — "
            f"found={found_count} deleted={deleted_count} "
            f"time={elapsed:.1f}s rate={rate:.1f}msg/s "
            f"floodwaits={total_floodwaits}"
        )

        return BotSliceResult(
            bot_idx=bot_idx, username=username,
            range_start=range_start, range_end=range_end,
            found=found_count, deleted=deleted_count,
            elapsed_seconds=elapsed, rate=rate, resumed=resumed,
            floodwait_count=total_floodwaits,
        )

    async def _fetch_with_retry(
        self, bot: Bot, channel: str | int, ids: list[int],
    ) -> tuple[list[Any], int, Exception | None]:
        """Fetch a batch with FloodWait retry.

        Returns (messages, floodwait_count, error).
        - If successful: (msgs, fw_count, None)
        - If FloodWait exhausted: ([], fw_count, FloodWait exception)
        - If other error: ([], 0, exception)

        Uses bot.call_unbounded (no per-bot serialization) since each bot
        only ever runs one task at a time in this scanner.
        """
        fw_count = 0
        for attempt in range(self.policy.max_floodwait_retries):
            try:
                msgs = await bot.call_unbounded(
                    lambda: bot.client.raw.get_messages(
                        channel, ids, replies=self.policy.replies,
                    )
                )
                if msgs is None:
                    return [], fw_count, None
                if not isinstance(msgs, list):
                    msgs = [msgs]
                return msgs, fw_count, None
            except FloodWait as e:
                fw_count += 1
                bot.stats.floodwait_count += 1
                logger.warning(
                    f"bot{bot.idx} @{bot.username} FloodWait {e.value}s "
                    f"(attempt {attempt+1}/{self.policy.max_floodwait_retries}) "
                    f"— sleeping {e.value + self.policy.fw_margin_s}s"
                )
                await asyncio.sleep(e.value + self.policy.fw_margin_s)
                continue
            except Exception as e:
                bot.stats.error_count += 1
                # For non-FloodWait errors, retry up to 3 times with backoff
                if attempt < 3:
                    backoff = 2 ** attempt
                    logger.warning(
                        f"bot{bot.idx} @{bot.username} error: {type(e).__name__}: {e} "
                        f"— retrying in {backoff}s"
                    )
                    await asyncio.sleep(backoff)
                    continue
                return [], fw_count, e
        # Exhausted all FloodWait retries
        return [], fw_count, FloodWait(0)  # synthetic; the caller will mark slice failed
