"""Config subsystem: typed schema + JSON loader."""

from __future__ import annotations

from tgkit.config.schema import (
    Config, ApiConfig, BotEntry, ChannelsConfig, VaultConfig,
    ThrottleConfig, ScanConfig, DbConfig, LoggingConfig,
)
from tgkit.config.loader import (
    load_config, save_config, find_config_path, get_default_config_path,
)

__all__ = [
    "Config", "ApiConfig", "BotEntry", "ChannelsConfig", "VaultConfig",
    "ThrottleConfig", "ScanConfig", "DbConfig", "LoggingConfig",
    "load_config", "save_config", "find_config_path", "get_default_config_path",
]
