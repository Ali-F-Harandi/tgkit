"""ThrottlePolicy — adaptive per-bot delay with backoff/decay.

Three orthogonal throttling mechanisms in tgkit (do NOT merge them):

1. ThrottlePolicy (THIS FILE) — per-bot, write-path
   Governs serialized heavy calls (upload, copy, forward, reupload).
   Adaptive: increases delay on FloodWait, decays toward min_interval on success.

2. ChannelCooldown (THIS FILE) — per-channel, all paths
   A dict[channel_id, next_allowed_time] consulted by AsyncBotPool.
   Prevents per-channel FloodWait even with N bots hitting the same channel.

3. BatchPolicy (scan/scanner.py, Phase 1) — scan read-path
   batch_size + concurrency, shrinks on FloodWait. Uses Bot.call_unbounded().
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ThrottlePolicy:
    """Adaptive per-bot throttle.

    Usage:
        policy = ThrottlePolicy()
        await policy.wait()          # sleep enough to respect current_delay
        try:
            result = await some_rpc()
            policy.on_success()       # decay toward min_interval
        except FloodWait as e:
            policy.on_floodwait(e.value)  # backoff
            await asyncio.sleep(e.value + 1)

    State:
        current_delay: starts at DEFAULT_DELAY (0.5s), adapts over time.
        _last_call_time: monotonic timestamp of last wait() return.
        _success_count: counter for decay_every.
    """
    min_interval: float = 0.3        # floor: never go faster than this
    max_interval: float = 30.0       # ceiling: never throttle harder than this
    backoff_factor: float = 1.5      # multiply delay by this on FloodWait
    decay_factor: float = 0.9        # multiply delay by this every `decay_every` successes
    decay_every: int = 10
    current_delay: float = field(default=0.5)  # mutable; starts at 0.5s

    # Internal state (not serialized)
    _last_call_time: float = field(default=0.0, repr=False)
    _success_count: int = field(default=0, repr=False)

    def reset(self) -> None:
        """Reset to initial state (e.g., after long idle)."""
        self.current_delay = 0.5
        self._last_call_time = 0.0
        self._success_count = 0

    async def wait(self) -> None:
        """Sleep enough to respect current_delay since last call."""
        now = time.perf_counter()
        if self._last_call_time > 0:
            elapsed = now - self._last_call_time
            wait_time = self.current_delay - elapsed
            if wait_time > 0:
                await asyncio.sleep(wait_time)
        self._last_call_time = time.perf_counter()

    def on_success(self) -> None:
        """Call after a successful RPC. Gradually relaxes the throttle."""
        self._success_count += 1
        if self._success_count >= self.decay_every:
            self._success_count = 0
            self.current_delay = max(
                self.min_interval,
                self.current_delay * self.decay_factor,
            )

    def on_floodwait(self, retry_after: float) -> None:
        """Call after a FloodWait. Increases the throttle.

        Sets delay to max(current_delay, (retry_after + 1) * backoff_factor).
        """
        new_delay = (retry_after + 1) * self.backoff_factor
        self.current_delay = min(
            self.max_interval,
            max(self.current_delay, new_delay),
        )
        # Reset success count — we just got rate-limited
        self._success_count = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize static config (not runtime state)."""
        return {
            "min_interval": self.min_interval,
            "max_interval": self.max_interval,
            "backoff_factor": self.backoff_factor,
            "decay_factor": self.decay_factor,
            "decay_every": self.decay_every,
        }


@dataclass
class ChannelCooldown:
    """Per-channel cooldown tracker.

    Even with N bots, hammering one channel triggers Telegram's per-channel
    FloodWait. This tracks next-allowed-time per channel and is consulted
    by AsyncBotPool.get_for_channel().

    Usage:
        cooldown = ChannelCooldown(extra_delay=0.2)
        await cooldown.wait_for(channel_id)  # sleep if needed
        # ... do RPC ...
        # On FloodWait:
        cooldown.trigger(channel_id, retry_after)
    """
    extra_delay: float = 0.2  # minimum spacing between calls to same channel
    _cooldowns: dict[int, float] = field(default_factory=dict, repr=False)

    async def wait_for(self, channel_id: int) -> None:
        """Sleep if channel is on cooldown."""
        next_allowed = self._cooldowns.get(channel_id, 0.0)
        if next_allowed > 0:
            now = time.perf_counter()
            wait_time = next_allowed - now
            if wait_time > 0:
                await asyncio.sleep(wait_time)

    def trigger(self, channel_id: int, retry_after: float) -> None:
        """Mark a channel as rate-limited for retry_after + 1 seconds."""
        self._cooldowns[channel_id] = time.perf_counter() + retry_after + 1

    def record_call(self, channel_id: int) -> None:
        """Record that a call was made to this channel (for spacing)."""
        self._cooldowns[channel_id] = time.perf_counter() + self.extra_delay

    def is_cooled_down(self, channel_id: int) -> bool:
        """Check if channel is past its cooldown."""
        next_allowed = self._cooldowns.get(channel_id, 0.0)
        return time.perf_counter() >= next_allowed

    def clear(self, channel_id: int | None = None) -> None:
        """Clear cooldown for one channel or all."""
        if channel_id is None:
            self._cooldowns.clear()
        else:
            self._cooldowns.pop(channel_id, None)
