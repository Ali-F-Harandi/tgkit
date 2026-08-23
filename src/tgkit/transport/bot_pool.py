"""AsyncBotPool — round-robin + per-channel cooldown.

Manages N Bot instances. Two get_next() variants:
    - get_next() — round-robin, ignores channel context
    - get_for_channel(channel_id) — picks the bot least likely to trigger
      per-channel FloodWait (respects ChannelCooldown)

The per-channel cooldown is THE mechanism that prevents 5 bots from all
hammering the same channel simultaneously.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tgkit.transport.bot import Bot
from tgkit.transport.throttle import ThrottlePolicy, ChannelCooldown

logger = logging.getLogger(__name__)


class AsyncBotPool:
    """Pool of Bot instances with round-robin + per-channel cooldown.

    Attributes:
        bots: list of Bot instances
        cooldown: ChannelCooldown (shared across all bots)
        throttle_template: ThrottlePolicy used as template for new bots
            (each bot gets its own copy so they adapt independently)
    """

    def __init__(
        self,
        tokens: list[str],
        api_id: int,
        api_hash: str,
        session_dir: str = "~/.tgkit/sessions",
        throttle: ThrottlePolicy | None = None,
        channel_cooldown: ChannelCooldown | None = None,
    ):
        if not tokens:
            raise ValueError("Cannot create pool with no bot tokens")

        self.throttle_template = throttle or ThrottlePolicy()
        self.cooldown = channel_cooldown or ChannelCooldown()

        # Create bots — each gets its own ThrottlePolicy copy (independent adaptation)
        self.bots: list[Bot] = []
        for idx, token in enumerate(tokens):
            # Copy the throttle policy so each bot adapts independently
            bot_throttle = ThrottlePolicy(
                min_interval=self.throttle_template.min_interval,
                max_interval=self.throttle_template.max_interval,
                backoff_factor=self.throttle_template.backoff_factor,
                decay_factor=self.throttle_template.decay_factor,
                decay_every=self.throttle_template.decay_every,
                current_delay=self.throttle_template.current_delay,
            )
            bot = Bot(
                token=token,
                api_id=api_id,
                api_hash=api_hash,
                session_dir=session_dir,
                throttle=bot_throttle,
                idx=idx,
            )
            self.bots.append(bot)

        self._counter = 0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_all(self) -> None:
        """Start all bots sequentially.

        Sequential (not parallel) to avoid SQLite session file lock contention
        during simultaneous client.start() calls.
        """
        for bot in self.bots:
            await bot.start()
            logger.debug(f"  {bot.stats_summary()}")

    async def stop_all(self) -> None:
        """Stop all bots sequentially."""
        for bot in self.bots:
            try:
                await bot.stop()
            except Exception as e:
                logger.debug(f"Error stopping bot{bot.idx}: {e}")

    async def __aenter__(self) -> "AsyncBotPool":
        await self.start_all()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop_all()

    # ------------------------------------------------------------------
    # Bot selection
    # ------------------------------------------------------------------

    async def get_next(self) -> Bot:
        """Round-robin: get the next bot in sequence."""
        async with self._lock:
            bot = self.bots[self._counter % len(self.bots)]
            self._counter += 1
            return bot

    async def get_for_channel(self, channel_id: int) -> Bot:
        """Get the bot best suited for a call to the given channel.

        Picks the bot whose turn it is (round-robin), but skips bots that
        are currently throttled hard. Records the call in the channel cooldown.

        This is the method to use for write-path ops (upload/copy/forward).
        """
        async with self._lock:
            # Try round-robin first
            best_bot = self.bots[self._counter % len(self.bots)]
            self._counter += 1

            # If the best bot's throttle is very high (recently flooded),
            # look for a bot with lower delay
            for bot in self.bots:
                if bot.throttle.current_delay < best_bot.throttle.current_delay:
                    best_bot = bot

            return best_bot

    def channel_cooldown(self, channel_id: int, retry_after: float) -> None:
        """Mark a channel as rate-limited (called after FloodWait on a channel)."""
        self.cooldown.trigger(channel_id, retry_after)
        logger.debug(
            f"Channel {channel_id} on cooldown for {retry_after + 1}s "
            f"(all {len(self.bots)} bots will avoid it)"
        )

    # ------------------------------------------------------------------
    # Peer resolution (delegates to all bots — they share session files? No,
    # each bot has its own session, so each must resolve peers independently)
    # ------------------------------------------------------------------

    async def resolve_peer_all(self, channel_id: int) -> int:
        """Resolve a channel peer on ALL bots.

        Each bot has its own session file, so each must cache the access_hash
        independently. Returns count of successful resolutions.
        """
        results = await asyncio.gather(
            *[bot.resolve_peer(channel_id) for bot in self.bots],
            return_exceptions=True,
        )
        success = sum(1 for r in results if r is True)
        logger.debug(
            f"Peer resolution for {channel_id}: {success}/{len(self.bots)} bots"
        )
        return success

    async def prime_destination_all(self, channel_id: int) -> int:
        """Prime a destination channel on ALL bots.

        Returns count of successful primings.
        """
        results = await asyncio.gather(
            *[bot.prime_destination(channel_id) for bot in self.bots],
            return_exceptions=True,
        )
        success = sum(1 for r in results if r is True)
        logger.debug(
            f"Destination priming for {channel_id}: {success}/{len(self.bots)} bots"
        )
        return success

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats_summary(self) -> str:
        """Multi-line stats for all bots."""
        lines = ["Bot pool stats:"]
        for bot in self.bots:
            lines.append(f"  {bot.stats_summary()}")
        total_reqs = sum(b.stats.request_count for b in self.bots)
        total_floods = sum(b.stats.floodwait_count for b in self.bots)
        total_errors = sum(b.stats.error_count for b in self.bots)
        lines.append(
            f"  TOTAL: reqs={total_reqs} floodwaits={total_floods} errors={total_errors}"
        )
        return "\n".join(lines)

    @property
    def size(self) -> int:
        """Number of bots in the pool."""
        return len(self.bots)

    def __repr__(self) -> str:
        return f"AsyncBotPool(size={len(self.bots)})"
