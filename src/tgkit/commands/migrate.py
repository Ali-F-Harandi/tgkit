"""`tgkit migrate` — import settings from legacy tg-vault."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path

from rich.console import Console

from tgkit.config.schema import Config
from tgkit.config.loader import save_config, get_default_config_path
from tgkit.db.connection import Database

logger = logging.getLogger(__name__)
console = Console()


async def cmd_migrate(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Migrate from legacy tg-vault (~/.tg-vault.json + tg-vault.db)."""
    import os

    def _find_legacy() -> tuple[Path | None, Path | None]:
        """Search common tg-vault locations: env override → home → cwd."""
        home_cfg = Path.home() / ".tg-vault.json"
        home_db = Path.home() / "tg-vault.db"

        # 1. Classic install: config in home dir
        if home_cfg.exists():
            return home_cfg, home_db if home_db.exists() else None

        # 2. Repo checkout: env override → ~/tg-vault → ./tg-vault
        candidates: list[Path] = []
        env_dir = os.environ.get("TG_VAULT_DIR")
        if env_dir:
            candidates.append(Path(env_dir))
        candidates += [
            Path.home() / "tg-vault",
            Path.cwd() / "tg-vault",
            Path("/home/z/my-project/tg-vault"),  # agent workspace fallback
        ]
        for root in candidates:
            cfg = root / "config.json"
            if cfg.exists():
                return cfg, root / "tg-vault.db"
        return None, None

    legacy_config_path, legacy_db_path = _find_legacy()

    console.print(f"[bold]Migration from legacy tg-vault[/bold]\n")

    found_anything = False

    # ── Migrate config ──
    if legacy_config_path.exists():
        console.print(f"[green]✓[/green] Found legacy config: {legacy_config_path}")
        found_anything = True

        try:
            legacy = json.loads(legacy_config_path.read_text(encoding="utf-8"))

            # Import bots
            if "bots" in legacy:
                for b in legacy["bots"]:
                    if isinstance(b, dict):
                        token = b.get("token", "")
                        username = b.get("username")
                    else:
                        token = str(b)
                        username = None
                    if token and not config.find_bot_by_token(token):
                        config.add_bot(token, username)
                        console.print(f"  [green]✓[/green] Imported bot: @{username or '?'}")

            # Import API credentials
            if legacy.get("api_id") and not config.api.api_id:
                config.api.api_id = int(legacy["api_id"])
                config.api.api_hash = str(legacy.get("api_hash", ""))
                console.print(f"  [green]✓[/green] Imported API credentials (api_id={config.api.api_id})")

            # Import channels
            if "channels" in legacy:
                ch = legacy["channels"]
                if ch.get("main") and not config.channels.vault_main:
                    config.channels.vault_main = int(ch["main"])
                    console.print(f"  [green]✓[/green] Imported vault_main: {ch['main']}")
                if ch.get("temp") and not config.channels.vault_temp:
                    config.channels.vault_temp = int(ch["temp"])
                if ch.get("storage"):
                    for s in ch["storage"]:
                        if s not in config.channels.vault_storage:
                            config.channels.vault_storage.append(int(s))

            # Import DB settings
            if legacy.get("db_enabled"):
                config.db.enabled = True
            if legacy.get("db_path"):
                config.db.path = legacy["db_path"]
            if legacy.get("db_sync_channel"):
                config.channels.db_sync = int(legacy["db_sync_channel"])
            if legacy.get("db_sync_msg_id"):
                config.db.sync_msg_id = int(legacy["db_sync_msg_id"])

            # Import chunk size
            if legacy.get("chunk_size_mb"):
                config.vault.chunk_size_mb = int(legacy["chunk_size_mb"])

            console.print(f"  [green]✓[/green] Config imported")

        except Exception as e:
            console.print(f"  [red]✗[/red] Failed to import config: {e}")
    else:
        console.print(f"[yellow]⚠[/yellow] No legacy config found at {legacy_config_path}")

    # ── Migrate DB ──
    if legacy_db_path.exists():
        console.print(f"\n[green]✓[/green] Found legacy DB: {legacy_db_path}")
        found_anything = True

        new_db_path = config.get_db_path()
        new_db_path.parent.mkdir(parents=True, exist_ok=True)

        if new_db_path.exists() and not args.force:
            console.print(f"  [yellow]⚠[/yellow] New DB already exists at {new_db_path}")
            console.print(f"    Use --force to overwrite")
        else:
            try:
                # Copy the legacy DB
                new_db_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_db_path, new_db_path)

                # Initialize new tables (additive)
                db = Database(new_db_path)
                db.init()

                console.print(f"  [green]✓[/green] DB copied to {new_db_path}")
                console.print(f"  [green]✓[/green] Schema migrated (new tables added)")
            except Exception as e:
                console.print(f"  [red]✗[/red] Failed to migrate DB: {e}")
    else:
        console.print(f"\n[yellow]⚠[/yellow] No legacy DB found at {legacy_db_path}")

    if not found_anything:
        console.print(f"\n[yellow]⚠[/yellow] No legacy tg-vault files found.")
        console.print(f"  Looked for:")
        console.print(f"    Config: {legacy_config_path}")
        console.print(f"    DB:     {legacy_db_path}")
        return 1

    # Save updated config
    save_config(config, config_path)
    console.print(f"\n[green]✓[/green] Migration complete! Config saved to {config_path}")
    console.print(f"  Run [bold]tgkit status[/bold] to verify.")

    return 0
