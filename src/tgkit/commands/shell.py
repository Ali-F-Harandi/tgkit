"""`tgkit shell` — interactive REPL for running commands.

A thin wrapper over the CLI. Every command is the same as the CLI;
the shell just provides history + tab-completion + convenience.

Usage:
    tgkit shell
    > bot list
    > scan run https://t.me/yxafile/100 --end-id 50
    > copy --from-scan 1
    > exit
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shlex
import sys
from pathlib import Path

from rich.console import Console

from tgkit.config.schema import Config
from tgkit.config.loader import load_config, find_config_path

logger = logging.getLogger(__name__)
console = Console()


async def cmd_shell(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Start an interactive REPL."""
    console.print("[bold]tgkit shell[/bold] — type 'help' for commands, 'exit' to quit\n")

    # Build the same parser as CLI
    from tgkit.cli import build_parser, dispatch
    parser = build_parser()

    while True:
        try:
            # Read input
            line = console.input("[bold cyan]tgkit>[/bold cyan] ")
            line = line.strip()

            if not line:
                continue

            if line.lower() in ("exit", "quit", "q"):
                console.print("[dim]Goodbye![/dim]")
                return 0

            if line.lower() in ("help", "?"):
                _print_help()
                continue

            # Parse and dispatch
            try:
                cli_args = parser.parse_args(shlex.split(line))
            except SystemExit:
                # argparse calls sys.exit on error — catch it
                continue

            # Reload config (in case it changed)
            config = load_config(config_path)

            # Dispatch
            try:
                exit_code = await dispatch(cli_args, config, config_path)
                if exit_code and exit_code != 0:
                    console.print(f"[dim](exit code: {exit_code})[/dim]")
            except Exception as e:
                console.print(f"[red]✗[/red] {type(e).__name__}: {e}")

            console.print()  # blank line between commands

        except KeyboardInterrupt:
            console.print("\n[dim]Use 'exit' to quit[/dim]")
        except EOFError:
            console.print("\n[dim]Goodbye![/dim]")
            return 0


def _print_help() -> None:
    """Print available commands."""
    console.print("[bold]Available commands:[/bold]")
    commands = [
        ("status", "Show capabilities + config + DB sync state"),
        ("stats", "Show overall statistics"),
        ("bot list", "List configured bots"),
        ("bot test", "Test bot connectivity"),
        ("channel list", "List registered channels"),
        ("scan run <link>", "Scan a channel (e.g., scan run https://t.me/yxafile/100)"),
        ("scan status", "Show scan status"),
        ("copy <link>", "Copy a message (no forward header)"),
        ("copy --from-scan <id>", "Copy from scan results"),
        ("forward <link>", "Forward a message (with header)"),
        ("reupload <link>", "Download + re-upload (breaks copyright)"),
        ("post create --links-file <f>", "Create linked-list post"),
        ("edit --channel <ch> --msg-id <id> --caption <text>", "Edit message"),
        ("delete --channel <ch> --msg-id <id>", "Delete message"),
        ("library list", "List library entries"),
        ("library search <query>", "Search library"),
        ("tag add <id> <tag>", "Add tag to library entry"),
        ("log --limit 10", "Show recent operations"),
        ("db sync", "Upload DB to channel"),
        ("db status", "Show DB sync status"),
        ("vault upload <file>", "Upload encrypted vault file"),
        ("vault download <link>", "Download vault file"),
        ("exit", "Quit the shell"),
    ]
    for cmd, desc in commands:
        console.print(f"  [cyan]{cmd:50s}[/cyan] [dim]{desc}[/dim]")
