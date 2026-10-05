"""tgkit — Unified Telegram toolkit.

Public API surface. Import from here for stable access:
    from tgkit import Config, TgClient, Bot, AsyncBotPool
    from tgkit.models import Channel, MessageRef, MessageInfo
"""

from __future__ import annotations

# Single source of truth: pyproject.toml. Read lazily so the package can be
# imported even if metadata is unavailable (e.g. vendored source checkout).
def _get_version() -> str:
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("tgkit")
        except PackageNotFoundError:
            pass
    except ImportError:
        pass
    return "0.3.0"  # fallback for source checkouts without metadata


__version__ = _get_version()
__author__ = "Ali-F-Harandi"
__license__ = "MIT"

# Config
from tgkit.config.schema import Config
from tgkit.config.loader import load_config, save_config, find_config_path

# Models
from tgkit.models.channel import Channel
from tgkit.models.message import MessageRef, MessageInfo
from tgkit.models.media import MediaInfo
from tgkit.models.link import parse_link, build_link, parse_channel_ref

# Transport
from tgkit.transport.client import TgClient
from tgkit.transport.bot import Bot, BotStats
from tgkit.transport.bot_pool import AsyncBotPool
from tgkit.transport.throttle import ThrottlePolicy, ChannelCooldown
from tgkit.transport.peer import PeerResolver
from tgkit.transport.errors import FloodWaitError, is_flood_wait, classify_error

__all__ = [
    "__version__",
    "__author__",
    "__license__",
    # Config
    "Config",
    "load_config",
    "save_config",
    "find_config_path",
    # Models
    "Channel",
    "MessageRef",
    "MessageInfo",
    "MediaInfo",
    "parse_link",
    "build_link",
    "parse_channel_ref",
    # Transport
    "TgClient",
    "Bot",
    "BotStats",
    "AsyncBotPool",
    "ThrottlePolicy",
    "ChannelCooldown",
    "PeerResolver",
    "FloodWaitError",
    "is_flood_wait",
    "classify_error",
]
