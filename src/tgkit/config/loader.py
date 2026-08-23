"""Config loader: find, load, save ~/.tgkit/config.json.

Search order:
    1. --config <path> CLI flag (highest priority)
    2. TGKIT_CONFIG env var
    3. ./tgkit.json (cwd)
    4. ~/.tgkit/config.json (default home)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tgkit.config.schema import Config


# ============================================================================
# Path resolution
# ============================================================================

def get_default_config_path() -> Path:
    """Default config path: ~/.tgkit/config.json"""
    return Path.home() / ".tgkit" / "config.json"


def find_config_path(explicit: str | None = None) -> Path:
    """Resolve config path with search order.

    Args:
        explicit: path from --config flag (highest priority)

    Returns:
        Path to config file (may not exist yet)
    """
    # 1. Explicit --config flag
    if explicit:
        return Path(explicit).expanduser()

    # 2. TGKIT_CONFIG env var
    env_path = os.environ.get("TGKIT_CONFIG")
    if env_path:
        return Path(env_path).expanduser()

    # 3. ./tgkit.json in cwd
    cwd_config = Path.cwd() / "tgkit.json"
    if cwd_config.exists():
        return cwd_config

    # 4. Default home location
    return get_default_config_path()


# ============================================================================
# Load / Save
# ============================================================================

def load_config(path: str | Path | None = None) -> Config:
    """Load config from path (or default location).

    If file doesn't exist, returns a default Config (caller should prompt to run `tgkit init`).
    """
    config_path = find_config_path(path) if path else get_default_config_path()

    if not config_path.exists():
        return Config()

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ConfigError(f"Failed to read config at {config_path}: {e}") from e

    return Config.from_dict(data)


def save_config(config: Config, path: str | Path | None = None) -> Path:
    """Save config to path (or default location).

    Creates parent directories with restrictive permissions (0700) since the file
    contains bot tokens.
    """
    config_path = find_config_path(path) if path else get_default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Write with restrictive permissions (config contains bot tokens)
    config_path.write_text(
        json.dumps(config.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        config_path.chmod(0o600)
    except OSError:
        # Filesystem may not support chmod (e.g., Windows) — non-fatal
        pass

    return config_path


def create_default_config_file(path: str | Path | None = None) -> Path:
    """Create a default config file with placeholder values.

    Used by `tgkit init`.
    """
    config = Config()
    # Leave api_id/api_hash empty — user must fill them
    return save_config(config, path)


# ============================================================================
# Exceptions
# ============================================================================

class ConfigError(Exception):
    """Config-related error."""
    pass


# ============================================================================
# Migration helpers (for future use)
# ============================================================================

def detect_legacy_tg_vault_config() -> Path | None:
    """Check if a legacy ~/.tg-vault.json exists (for future migration)."""
    legacy = Path.home() / ".tg-vault.json"
    return legacy if legacy.exists() else None


def import_legacy_config(legacy_path: Path) -> Config:
    """Import settings from a legacy tg-vault config.

    Future feature — not used in Phase 0 but defined here for completeness.
    """
    data = json.loads(legacy_path.read_text(encoding="utf-8"))
    config = Config()

    # Map old fields to new structure
    if "api_id" in data:
        config.api.api_id = int(data["api_id"])
    if "api_hash" in data:
        config.api.api_hash = str(data["api_hash"])
    if "bots" in data:
        config.bots = [BotEntry.from_dict(b) if isinstance(b, dict) else BotEntry(token=str(b))
                       for b in data["bots"]]
    if "channels" in data:
        ch = data["channels"]
        config.channels.vault_main = ch.get("main")
        config.channels.vault_temp = ch.get("temp")
        config.channels.vault_storage = list(ch.get("storage", []))

    return config
