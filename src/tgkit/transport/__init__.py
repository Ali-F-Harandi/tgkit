"""Transport subsystem: async MTProto via pyrofork."""

from __future__ import annotations

from tgkit.transport.client import TgClient
from tgkit.transport.bot import Bot, BotStats
from tgkit.transport.bot_pool import AsyncBotPool
from tgkit.transport.throttle import ThrottlePolicy, ChannelCooldown
from tgkit.transport.peer import PeerResolver
from tgkit.transport.errors import (
    FloodWaitError, TransportError, is_flood_wait, classify_error,
)

__all__ = [
    "TgClient",
    "Bot",
    "BotStats",
    "AsyncBotPool",
    "ThrottlePolicy",
    "ChannelCooldown",
    "PeerResolver",
    "FloodWaitError",
    "TransportError",
    "is_flood_wait",
    "classify_error",
]
