"""`tgkit init` — create a default config file."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from rich.console import Console

from tgkit.config.loader import (
    load_config, save_config, get_default_config_path, ConfigError,
)

logger = logging.getLogger(__name__)
console = Console()


async def cmd_init(args: argparse.Namespace, config_path: Path) -> int:
    """Create a default config file at the given (or default) path.

    If the file already exists, refuses unless --force.
    """
    target = Path(args.config).expanduser() if args.config else get_default_config_path()

    if target.exists() and not args.force:
        console.print(f"[yellow]Config already exists at:[/yellow] {target}")
        console.print("Use [bold]--force[/bold] to overwrite.")
        return 1

    # Create empty config
    from tgkit.config.schema import Config
    config = Config()

    # Save
    save_config(config, target)
    console.print(f"[green]✓[/green] Created config at: [bold]{target}[/bold]")
    console.print()
    console.print("[dim]Next steps:[/dim]")
    console.print(f"  1. Edit [bold]{target}[/bold] and fill in api_id / api_hash")
    console.print(f"     (get them from https://my.telegram.org → API Development Tools)")
    console.print(f"  2. Add bots:  [bold]tgkit bot add <TOKEN>[/bold]")
    console.print(f"  3. Test:      [bold]tgkit bot test[/bold]")
    console.print(f"  4. Add channels: [bold]tgkit channel add @yxafile --role source[/bold]")

    return 0
