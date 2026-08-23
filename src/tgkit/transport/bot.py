"""Bot — TgClient + ThrottlePolicy + stats. The unified SmartBot.

Replaces:
    - tg_vault.bot_pool.Bot / HybridBot (threading + Bot API)
    - tg-vault-tool.SmartBot (async + adaptive throttle)
    - tg-scan-turbo.AsyncBotWorker (async + no throttle)

One class, one concurrency model (asyncio), one transport (pyrofork).

Key methods:
    await bot.start()
    result = await bot.call(coro_factory)          # serialized, throttled
    result = await bot.call_unbounded(coro_factory) # concurrent, no per-bot lock
    await bot.stop()

The distinction between call() and call_unbounded() is THE key insight:
    - call() acquires self._lock → serializes heavy ops (upload, forward, copy)
    - call_unbounded() skips the lock → allows N concurrent RPCs on one session
      (used by BatchedScanner for batched get_messages)

Both apply the throttle; the difference is whether they block other callers
on the same bot.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pyrogram.errors import FloodWait

from tgkit.transport.client import TgClient
from tgkit.transport.throttle import ThrottlePolicy
from tgkit.transport.errors import classify_error

logger = logging.getLogger(__name__)

# Type alias: a factory that returns a fresh coroutine
CoroFactory = Callable[[], Awaitable[Any]]


@dataclass
class BotStats:
    """Runtime stats for a bot."""
    request_count: int = 0
    floodwait_count: int = 0
    error_count: int = 0
    last_request_at: float = 0.0
    last_floodwait_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_count": self.request_count,
            "floodwait_count": self.floodwait_count,
            "error_count": self.error_count,
            "last_request_at": self.last_request_at,
            "last_floodwait_at": self.last_floodwait_at,
        }


class Bot:
    """A single bot with adaptive throttling.

    Attributes:
        client: TgClient (owns pyrofork Client + PeerResolver)
        throttle: ThrottlePolicy (adaptive per-bot delay)
        stats: BotStats (request/floodwait/error counts)
        idx: Index in the pool (for round-robin identification)
    """

    def __init__(
        self,
        token: str,
        api_id: int,
        api_hash: str,
        session_dir: str = "~/.tgkit/sessions",
        throttle: ThrottlePolicy | None = None,
        idx: int = 0,
    ):
        self.client = TgClient(token, api_id, api_hash, session_dir)
        self.throttle = throttle or ThrottlePolicy()
        self.stats = BotStats()
        self.idx = idx
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the underlying TgClient."""
        await self.client.start()

    async def stop(self) -> None:
        """Stop the underlying TgClient."""
        await self.client.stop()

    @property
    def bot_id(self) -> int:
        return self.client.bot_id

    @property
    def username(self) -> str | None:
        return self.client.username

    @property
    def started(self) -> bool:
        return self.client.started

    # ------------------------------------------------------------------
    # Peer resolution (delegates to TgClient)
    # ------------------------------------------------------------------

    async def resolve_peer(self, channel_id: int) -> bool:
        """Resolve a channel peer."""
        return await self.client.resolve_peer(channel_id)

    async def prime_destination(self, channel_id: int) -> bool:
        """Prime a destination channel."""
        return await self.client.prime_destination(channel_id)

    # ------------------------------------------------------------------
    # Throttled call — serialized (write-path)
    # ------------------------------------------------------------------

    async def call(self, coro_factory: CoroFactory, retries: int = 4) -> Any:
        """Execute a coroutine with throttling + FloodWait retry.

        Acquires self._lock → serializes all calls on this bot.
        Use for heavy operations (upload, forward, copy, reupload).

        Args:
            coro_factory: callable that returns a fresh coroutine
            retries: max retry attempts on FloodWait (default 4)

        Returns:
            The coroutine's result.

        Raises:
            The last exception if all retries fail.
        """
        async with self._lock:
            return await self._call_with_throttle(coro_factory, retries)

    # ------------------------------------------------------------------
    # Unbounded call — concurrent (scan read-path)
    # ------------------------------------------------------------------

    async def call_unbounded(self, coro_factory: CoroFactory, retries: int = 4) -> Any:
        """Execute a coroutine with throttling but WITHOUT the per-bot lock.

        Allows multiple concurrent RPCs on the same session (pyrofork multiplexes
        them on one TCP connection). Use for lightweight reads (batched get_messages).

        The throttle is still applied (waits current_delay since last call).

        Args:
            coro_factory: callable that returns a fresh coroutine
            retries: max retry attempts on FloodWait (default 4)
        """
        return await self._call_with_throttle(coro_factory, retries)

    # ------------------------------------------------------------------
    # Internal: throttled call with retry
    # ------------------------------------------------------------------

    async def _call_with_throttle(self, coro_factory: CoroFactory, retries: int) -> Any:
        """Shared throttle + retry logic for call() and call_unbounded()."""
        last_exc: Exception | None = None

        for attempt in range(retries):
            # Throttle: wait enough since last call
            await self.throttle.wait()

            try:
                self.stats.request_count += 1
                self.stats.last_request_at = time.perf_counter()
                result = await coro_factory()
                self.throttle.on_success()
                return result

            except FloodWait as e:
                self.stats.floodwait_count += 1
                self.stats.last_floodwait_at = time.perf_counter()
                self.throttle.on_floodwait(e.value)
                logger.debug(
                    f"Bot{self.idx} (@{self.username}) FloodWait {e.value}s "
                    f"on attempt {attempt + 1}/{retries}"
                )
                await asyncio.sleep(e.value + 1)
                last_exc = e
                continue

            except Exception as e:
                self.stats.error_count += 1
                info = classify_error(e)
                if not info["should_retry"] or attempt >= retries - 1:
                    raise
                # Transient — backoff and retry
                backoff = 2 ** attempt
                logger.debug(
                    f"Bot{self.idx} (@{self.username}) error on attempt {attempt + 1}: "
                    f"{type(e).__name__}: {e} — retrying in {backoff}s"
                )
                await asyncio.sleep(backoff)
                last_exc = e
                continue

        # Exhausted retries
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Bot{self.idx}: exhausted {retries} retries with no exception")

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def stats_summary(self) -> str:
        """One-line stats summary."""
        return (
            f"bot{self.idx} @{self.username or '?'}: "
            f"reqs={self.stats.request_count} "
            f"floodwaits={self.stats.floodwait_count} "
            f"errors={self.stats.error_count} "
            f"delay={self.throttle.current_delay:.2f}s"
        )

    def __repr__(self) -> str:
        return f"Bot(idx={self.idx}, bot_id={self.bot_id}, @{self.username or '?'})"
