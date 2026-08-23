"""Typed config schema — single source of truth for all tgkit settings.

All fields have sensible defaults; config.json overrides them, CLI flags override that.
Uses dataclasses for JSON (de)serialization via to_dict / from_dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ============================================================================
# Sub-configs
# ============================================================================

@dataclass
class ApiConfig:
    """Telegram API credentials (from https://my.telegram.org)."""
    api_id: int = 0
    api_hash: str = ""
    session_dir: str = "~/.tgkit/sessions"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ApiConfig":
        return cls(
            api_id=int(d.get("api_id", 0) or 0),
            api_hash=str(d.get("api_hash", "") or ""),
            session_dir=str(d.get("session_dir", "~/.tgkit/sessions")),
        )


@dataclass
class BotEntry:
    """A single bot token + cached username."""
    token: str
    username: str | None = None

    @property
    def bot_id(self) -> int:
        """Extract numeric bot ID from token (part before ':')."""
        try:
            return int(self.token.split(":")[0])
        except (IndexError, ValueError):
            return 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BotEntry":
        return cls(
            token=str(d.get("token", "")),
            username=d.get("username"),
        )


@dataclass
class ChannelsConfig:
    """Channel role assignments."""
    default_destination: int | None = None
    vault_main: int | None = None
    vault_temp: int | None = None
    vault_storage: list[int] = field(default_factory=list)
    db_sync: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChannelsConfig":
        return cls(
            default_destination=d.get("default_destination"),
            vault_main=d.get("vault_main"),
            vault_temp=d.get("vault_temp"),
            vault_storage=list(d.get("vault_storage", [])),
            db_sync=d.get("db_sync"),
        )


@dataclass
class VaultConfig:
    """Vault (chunked storage) subsystem settings."""
    chunk_size_mb: int = 19
    upload_delay: float = 0.3
    download_delay: float = 0.2
    parallel_workers: int = 4
    default_manifest_type: str = "text"  # text | file | auto
    large_file_threshold_mb: int = 45    # above this → use MTProto instead of Bot API
    compression: bool = True
    # Encryption (AES-256-GCM + PBKDF2-HMAC-SHA512, fixed for compat with tg-vault)
    encryption_algorithm: str = "aes-256-gcm"
    encryption_kdf: str = "pbkdf2-sha512"
    encryption_iterations: int = 600_000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VaultConfig":
        return cls(
            chunk_size_mb=int(d.get("chunk_size_mb", 19)),
            upload_delay=float(d.get("upload_delay", 0.3)),
            download_delay=float(d.get("download_delay", 0.2)),
            parallel_workers=int(d.get("parallel_workers", 4)),
            default_manifest_type=str(d.get("default_manifest_type", "text")),
            large_file_threshold_mb=int(d.get("large_file_threshold_mb", 45)),
            compression=bool(d.get("compression", True)),
            encryption_algorithm=str(d.get("encryption_algorithm", "aes-256-gcm")),
            encryption_kdf=str(d.get("encryption_kdf", "pbkdf2-sha512")),
            encryption_iterations=int(d.get("encryption_iterations", 600_000)),
        )


@dataclass
class ThrottleConfig:
    """Adaptive throttling settings.

    Three orthogonal mechanisms:
      1. Per-bot adaptive delay (this config) — write path (upload/copy/forward)
      2. Per-channel cooldown — handled in AsyncBotPool
      3. Batch policy (scan read path) — see ScanConfig
    """
    min_interval: float = 0.3        # floor: never go faster than this per bot
    max_interval: float = 30.0       # ceiling: never throttle harder than this
    backoff_factor: float = 1.5      # multiply delay by this on FloodWait
    decay_factor: float = 0.9        # multiply delay by this every `decay_every` successes
    decay_every: int = 10
    per_channel_cooldown: float = 0.2  # extra delay between calls to the same channel

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ThrottleConfig":
        return cls(
            min_interval=float(d.get("min_interval", 0.3)),
            max_interval=float(d.get("max_interval", 30.0)),
            backoff_factor=float(d.get("backoff_factor", 1.5)),
            decay_factor=float(d.get("decay_factor", 0.9)),
            decay_every=int(d.get("decay_every", 10)),
            per_channel_cooldown=float(d.get("per_channel_cooldown", 0.2)),
        )


@dataclass
class ScanConfig:
    """Channel scanner settings (batched + concurrent get_messages)."""
    batch_size: int = 50             # IDs per channels.GetMessages RPC
    concurrency: int = 5             # in-flight RPCs per bot
    save_every_n_batches: int = 10
    replies: int = 0                 # 0 = skip reply chain (halves work)
    checkpoint_interval: int = 250   # upload/update scan results every N messages (--continue mode)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScanConfig":
        return cls(
            batch_size=int(d.get("batch_size", 50)),
            concurrency=int(d.get("concurrency", 5)),
            save_every_n_batches=int(d.get("save_every_n_batches", 10)),
            replies=int(d.get("replies", 0)),
            checkpoint_interval=int(d.get("checkpoint_interval", 250)),
        )


@dataclass
class DbConfig:
    """SQLite database settings."""
    enabled: bool = True
    path: str | None = None          # None → ~/.tgkit/tgkit.db
    auto_sync: bool = False
    sync_multipart: bool = False
    sync_msg_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DbConfig":
        return cls(
            enabled=bool(d.get("enabled", True)),
            path=d.get("path"),
            auto_sync=bool(d.get("auto_sync", False)),
            sync_multipart=bool(d.get("sync_multipart", False)),
            sync_msg_id=d.get("sync_msg_id"),
        )


@dataclass
class LoggingConfig:
    """Logging settings."""
    level: str = "INFO"              # DEBUG | INFO | WARNING | ERROR
    file: str | None = None          # None → stderr only

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LoggingConfig":
        return cls(
            level=str(d.get("level", "INFO")),
            file=d.get("file"),
        )


# ============================================================================
# Top-level Config
# ============================================================================

@dataclass
class Config:
    """Top-level tgkit configuration.

    Stored as JSON at ~/.tgkit/config.json (or path from --config).
    Every field overridable by CLI flags.
    """
    version: int = 1
    api: ApiConfig = field(default_factory=ApiConfig)
    bots: list[BotEntry] = field(default_factory=list)
    channels: ChannelsConfig = field(default_factory=ChannelsConfig)
    vault: VaultConfig = field(default_factory=VaultConfig)
    throttle: ThrottleConfig = field(default_factory=ThrottleConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    db: DbConfig = field(default_factory=DbConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "api": self.api.to_dict(),
            "bots": [b.to_dict() for b in self.bots],
            "channels": self.channels.to_dict(),
            "vault": self.vault.to_dict(),
            "throttle": self.throttle.to_dict(),
            "scan": self.scan.to_dict(),
            "db": self.db.to_dict(),
            "logging": self.logging.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Config":
        return cls(
            version=int(d.get("version", 1)),
            api=ApiConfig.from_dict(d.get("api", {})),
            bots=[BotEntry.from_dict(b) for b in d.get("bots", [])],
            channels=ChannelsConfig.from_dict(d.get("channels", {})),
            vault=VaultConfig.from_dict(d.get("vault", {})),
            throttle=ThrottleConfig.from_dict(d.get("throttle", {})),
            scan=ScanConfig.from_dict(d.get("scan", {})),
            db=DbConfig.from_dict(d.get("db", {})),
            logging=LoggingConfig.from_dict(d.get("logging", {})),
        )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------
    @property
    def bot_tokens(self) -> list[str]:
        """All bot tokens as a flat list."""
        return [b.token for b in self.bots if b.token]

    def get_session_dir(self) -> Path:
        """Resolved session directory (expands ~)."""
        return Path(self.api.session_dir).expanduser()

    def get_db_path(self) -> Path:
        """Resolved DB path (None → ~/.tgkit/tgkit.db)."""
        if self.db.path:
            return Path(self.db.path).expanduser()
        return Path("~/.tgkit/tgkit.db").expanduser()

    def find_bot_by_token(self, token: str) -> BotEntry | None:
        for b in self.bots:
            if b.token == token:
                return b
        return None

    def find_bot_by_id(self, bot_id: int) -> BotEntry | None:
        for b in self.bots:
            if b.bot_id == bot_id:
                return b
        return None

    def add_bot(self, token: str, username: str | None = None) -> bool:
        """Add a bot. Returns True if added, False if already present."""
        if self.find_bot_by_token(token):
            return False
        self.bots.append(BotEntry(token=token, username=username))
        return True

    def remove_bot(self, index: int) -> BotEntry | None:
        """Remove a bot by index. Returns the removed entry or None if out of range."""
        if 0 <= index < len(self.bots):
            return self.bots.pop(index)
        return None

    def expected_throughput(self) -> float:
        """Rough estimate of requests/sec given bot count and throttle settings."""
        if not self.bots:
            return 0.0
        return len(self.bots) / self.throttle.min_interval
