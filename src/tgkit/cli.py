"""CLI entry point + command registry.

Usage:
    tgkit [--config <path>] [--version] <command> [subcommand] ...

Commands are registered in build_parser(). Each command module exports
async cmd_* functions with signature:
    async def cmd_xxx(args: argparse.Namespace, config: Config, ...) -> int
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

from tgkit import __version__
from tgkit.config.schema import Config
from tgkit.config.loader import load_config, find_config_path

logger = logging.getLogger(__name__)
console = Console()


def setup_logging(level: str = "INFO") -> None:
    """Configure logging with rich format.

    Suppresses noisy pyrofork logs unless --verbose.
    """
    try:
        from rich.logging import RichHandler
        logging.basicConfig(
            level=level,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
        )
    except ImportError:
        logging.basicConfig(level=level)

    # Suppress pyrofork's verbose INFO logs unless we're in DEBUG mode
    if level != "DEBUG":
        logging.getLogger("pyrogram").setLevel("WARNING")
        logging.getLogger("pyrogram.session").setLevel("WARNING")
        logging.getLogger("pyrogram.connection").setLevel("WARNING")
        logging.getLogger("pyrogram.dispatcher").setLevel("WARNING")


# ============================================================================
# Parser construction
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="tgkit",
        description="Unified Telegram toolkit: scan, copy, forward, vault, library",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Quick start:
  tgkit init                              # create config
  tgkit bot add <TOKEN>                   # add a bot
  tgkit bot test                          # verify connectivity
  tgkit channel add @yxafile --role source
  tgkit channel add -100... --role destination

For more: see https://github.com/Ali-F-Harandi/tgkit
""",
    )

    # Global flags
    parser.add_argument(
        "--config",
        help="Path to config file (default: ~/.tgkit/config.json)",
        default=None,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"tgkit {__version__}",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )

    # Subcommands
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")

    # ---- init ----
    p_init = sub.add_parser("init", help="Create a default config file")
    p_init.add_argument("--force", action="store_true", help="Overwrite if exists")
    p_init.set_defaults(_handler="init")

    # ---- status ----
    p_status = sub.add_parser("status", help="Show capabilities, config, and DB sync state")
    p_status.set_defaults(_handler="status")

    # ---- bot ----
    p_bot = sub.add_parser("bot", help="Bot management")
    bot_sub = p_bot.add_subparsers(dest="bot_cmd", required=True, metavar="<subcommand>")

    p_bot_add = bot_sub.add_parser("add", help="Add a bot token (validates via getMe)")
    p_bot_add.add_argument("token", help="Bot token from BotFather")
    p_bot_add.set_defaults(_handler="bot_add")

    p_bot_list = bot_sub.add_parser("list", help="List configured bots")
    p_bot_list.set_defaults(_handler="bot_list")

    p_bot_remove = bot_sub.add_parser("remove", help="Remove a bot by index")
    p_bot_remove.add_argument("index", type=int, help="Bot index from `bot list`")
    p_bot_remove.set_defaults(_handler="bot_remove")

    p_bot_test = bot_sub.add_parser("test", help="Test connectivity of all bots")
    p_bot_test.set_defaults(_handler="bot_test")

    # ---- channel ----
    p_channel = sub.add_parser("channel", help="Channel management")
    channel_sub = p_channel.add_subparsers(dest="channel_cmd", required=True, metavar="<subcommand>")

    p_ch_add = channel_sub.add_parser("add", help="Register a channel")
    p_ch_add.add_argument("channel_ref", help="Channel @username or -100 ID")
    p_ch_add.add_argument(
        "--role",
        choices=["source", "destination", "vault_main", "vault_temp", "vault_storage", "db_sync"],
        default="source",
        help="Role for this channel (default: source)",
    )
    p_ch_add.set_defaults(_handler="channel_add")

    p_ch_list = channel_sub.add_parser("list", help="List registered channels")
    p_ch_list.set_defaults(_handler="channel_list")

    p_ch_remove = channel_sub.add_parser("remove", help="Remove a channel")
    p_ch_remove.add_argument("channel_ref", help="Channel @username or -100 ID")
    p_ch_remove.set_defaults(_handler="channel_remove")

    # ---- db ----
    p_db = sub.add_parser("db", help="Database sync & management")
    db_sub = p_db.add_subparsers(dest="db_cmd", required=True, metavar="<subcommand>")

    p_db_sync = db_sub.add_parser("sync", help="Upload local DB to sync channel")
    p_db_sync.add_argument("--description", "-d", help="Description for this sync")
    p_db_sync.set_defaults(_handler="db_sync")

    p_db_download = db_sub.add_parser("download", help="Download DB from sync channel")
    p_db_download.set_defaults(_handler="db_download")

    p_db_find = db_sub.add_parser("find", help="Discover DB in channel by scanning")
    p_db_find.set_defaults(_handler="db_find")

    p_db_status = db_sub.add_parser("status", help="Show DB sync status + local stats")
    p_db_status.set_defaults(_handler="db_status")

    # ---- scan ----
    p_scan = sub.add_parser("scan", help="Scan a channel backwards from a message link")
    scan_sub = p_scan.add_subparsers(dest="scan_cmd", metavar="<subcommand>")

    p_scan_run = scan_sub.add_parser("run", help="Scan a channel (default subcommand)")
    p_scan_run.add_argument("link", help="Last message link (e.g., https://t.me/AWManga/33530)")
    p_scan_run.add_argument("--end-id", type=int, default=1, help="Lowest ID to scan (default 1)")
    p_scan_run.add_argument("--batch-size", type=int, default=None, help="IDs per RPC (default from config)")
    p_scan_run.add_argument("--concurrency", type=int, default=None, help="In-flight RPCs per bot")
    p_scan_run.add_argument("--output", "-o", help="Output file path (JSON or CSV)")
    p_scan_run.add_argument("--format", choices=["json", "csv"], help="Output format (auto-detect from extension)")
    p_scan_run.add_argument("--fresh", action="store_true", help="Don't resume, start fresh")
    p_scan_run.add_argument("--continue", action="store_true",
                           help="Enable continue mode: upload scan results every N messages (checkpoint_interval)")
    p_scan_run.add_argument("--continue-link", metavar="LINK",
                           help="Resume from a checkpoint link (downloads previous results, continues scanning)")
    p_scan_run.add_argument("--checkpoint-interval", type=int,
                           help=f"Messages between checkpoints (default: {50} from config)")
    p_scan_run.add_argument(
        "--parallel-ranges", action="store_true",
        help="Use ParallelRangeScanner: split the ID range across all bots, each bot "
             "scans its own slice. Faster (5x in benchmarks) and supports resume per-bot.",
    )
    p_scan_run.add_argument(
        "--inter-batch-sleep", type=float, default=None,
        help="Sleep between batches in seconds (parallel-ranges mode only, default 0.3)",
    )
    p_scan_run.set_defaults(_handler="scan_run")

    p_scan_resume = scan_sub.add_parser("resume", help="Resume an interrupted scan")
    p_scan_resume.add_argument("scan_id", type=int, help="Scan ID to resume")
    p_scan_resume.add_argument("--batch-size", type=int, default=None)
    p_scan_resume.add_argument("--concurrency", type=int, default=None)
    p_scan_resume.add_argument("--output", "-o")
    p_scan_resume.add_argument("--format", choices=["json", "csv"])
    p_scan_resume.set_defaults(_handler="scan_resume")

    p_scan_status = scan_sub.add_parser("status", help="Show scan status")
    p_scan_status.add_argument("scan_id", type=int, nargs="?", help="Specific scan ID (optional)")
    p_scan_status.set_defaults(_handler="scan_status")

    # ---- copy / forward / reupload (shared parser) ----
    for op_name in ("copy", "forward", "reupload"):
        p_op = sub.add_parser(op_name, help=f"{op_name} message(s) to destination channel")
        p_op.add_argument("link", nargs="?", help="Source message link (or use --from-scan/--file)")
        p_op.add_argument("--count", type=int, help="Number of messages (range mode)")
        p_op.add_argument("--reverse", action="store_true", help="Go backwards (lower IDs)")
        p_op.add_argument("--file", help="File with one link per line")
        p_op.add_argument("--from-scan", type=int, metavar="SCAN_ID",
                         help="Pull messages from a previous scan (the killer feature)")
        p_op.add_argument("--filter", help='Filter: "ext=.zip size=>10MB media=document"')
        p_op.add_argument("--to", help="Destination channel (default: config default_destination)")
        p_op.add_argument("--caption", help="Custom caption (copy/reupload only). Empty string = no caption")
        p_op.add_argument("--parallel", type=int, help="Max concurrent operations (default: bot count)")
        p_op.add_argument("--save-results", help="Save results JSON to this path")
        p_op.add_argument("--fresh", action="store_true", help="Don't resume, reprocess all")
        p_op.set_defaults(_handler="operation", _op_type=op_name)

    # ---- post ----
    p_post = sub.add_parser("post", help="Create linked-list posts with many links")
    post_sub = p_post.add_subparsers(dest="post_cmd", metavar="<subcommand>")

    p_post_create = post_sub.add_parser("create", help="Create a linked-list post")
    p_post_create.add_argument("--links-file", help='JSON file: [{"label":"CH1","url":"https://..."},...]')
    p_post_create.add_argument("--from-scan", type=int, metavar="SCAN_ID",
                              help="Build links from scan results + operations_log (copied files)")
    p_post_create.add_argument("--op-type", default="copy", choices=["copy", "forward", "reupload"],
                              help="Operation type to look up in operations_log (default: copy)")
    p_post_create.add_argument("--to", help="Destination channel (default: config default_destination)")
    p_post_create.add_argument("--header", help="Text before links (first message)")
    p_post_create.add_argument("--footer", help="Text after links (last message)")
    p_post_create.add_argument("--cover", help="Path to cover image (sent as photo on first message)")
    p_post_create.add_argument("--reply-to", type=int, help="Reply to an existing message ID")
    p_post_create.add_argument("--continue-label", default="→ ادامه", help="Label for continue link")
    p_post_create.add_argument("--links-per-line", type=int, default=3, help="Links per line (default 3)")
    p_post_create.set_defaults(_handler="post_create")

    # ---- edit ----
    p_edit = sub.add_parser("edit", help="Edit a message's caption/text/media in-place")
    p_edit.add_argument("--channel", required=True,
                       help="Channel ID (e.g., -1001234567890 or @username)")
    p_edit.add_argument("--msg-id", type=int, required=True, help="Message ID to edit")
    p_edit.add_argument("--caption", help="New caption (for media messages)")
    p_edit.add_argument("--text", help="New text (for text messages)")
    p_edit.add_argument("--media", help="Path to new media file (replaces in-place)")
    p_edit.add_argument("--media-type", choices=["document", "photo", "video", "animation", "audio"],
                       default="document", help="Media type for --media (default: document)")
    p_edit.set_defaults(_handler="edit")

    # ---- delete ----
    p_delete = sub.add_parser("delete", help="Delete messages from a channel")
    p_delete.add_argument("--channel", help="Channel ID (e.g., -1001234567890 or @username)")
    p_delete.add_argument("--msg-id", type=int, help="Message ID to delete")
    p_delete.add_argument("--count", type=int, help="Delete N consecutive messages (range mode)")
    p_delete.add_argument("--file", help="File with one ref per line (channel/msg_id)")
    p_delete.add_argument("--from-log", action="store_true",
                         help="Delete messages from operations_log (use with --op-type/--status)")
    p_delete.add_argument("--op-type", help="Filter --from-log by operation type")
    p_delete.add_argument("--status", help="Filter --from-log by status")
    p_delete.set_defaults(_handler="delete")

    # ---- tag ----
    p_tag = sub.add_parser("tag", help="Manage tags on library entries")
    tag_sub = p_tag.add_subparsers(dest="tag_cmd", required=True, metavar="<subcommand>")

    p_tag_add = tag_sub.add_parser("add", help="Add a tag to a library entry")
    p_tag_add.add_argument("library_id", type=int, help="Library entry ID")
    p_tag_add.add_argument("tag", help="Tag to add")
    p_tag_add.set_defaults(_handler="tag")

    p_tag_remove = tag_sub.add_parser("remove", help="Remove a tag from a library entry")
    p_tag_remove.add_argument("library_id", type=int, help="Library entry ID")
    p_tag_remove.add_argument("tag", help="Tag to remove")
    p_tag_remove.set_defaults(_handler="tag")

    p_tag_list = tag_sub.add_parser("list", help="List tags on a library entry")
    p_tag_list.add_argument("library_id", type=int, help="Library entry ID")
    p_tag_list.set_defaults(_handler="tag")

    p_tag_search = tag_sub.add_parser("search", help="Search library entries by tag")
    p_tag_search.add_argument("tag", help="Tag to search for")
    p_tag_search.set_defaults(_handler="tag")

    # ---- library ----
    p_library = sub.add_parser("library", help="Query and manage the library")
    library_sub = p_library.add_subparsers(dest="library_cmd", required=True, metavar="<subcommand>")

    p_lib_list = library_sub.add_parser("list", help="List library entries")
    p_lib_list.add_argument("--limit", type=int, default=20, help="Max entries (default 20)")
    p_lib_list.add_argument("--kind", choices=["vault", "copied", "single"], help="Filter by kind")
    p_lib_list.set_defaults(_handler="library")

    p_lib_search = library_sub.add_parser("search", help="Search library by name")
    p_lib_search.add_argument("query", help="Search query")
    p_lib_search.add_argument("--limit", type=int, default=50)
    p_lib_search.set_defaults(_handler="library")

    p_lib_info = library_sub.add_parser("info", help="Show detailed info about an entry")
    p_lib_info.add_argument("library_id", type=int, help="Library entry ID")
    p_lib_info.set_defaults(_handler="library")

    p_lib_stats = library_sub.add_parser("stats", help="Show library statistics")
    p_lib_stats.set_defaults(_handler="library")

    # ---- log ----
    p_log = sub.add_parser("log", help="Query operations log")
    p_log.add_argument("--op-type", help="Filter by operation type (copy/forward/reupload/scan/edit/delete/post_create)")
    p_log.add_argument("--status", help="Filter by status (ok/failed)")
    p_log.add_argument("--limit", type=int, default=20, help="Max entries (default 20)")
    p_log.add_argument("--stats", action="store_true", help="Show statistics instead of log entries")
    p_log.set_defaults(_handler="log")

    # ---- stats ----
    p_stats = sub.add_parser("stats", help="Show overall statistics")
    p_stats.set_defaults(_handler="stats")

    # ---- migrate ----
    p_migrate = sub.add_parser("migrate", help="Migrate from legacy tg-vault")
    p_migrate.add_argument("--force", action="store_true", help="Overwrite existing DB")
    p_migrate.set_defaults(_handler="migrate")

    # ---- shell ----
    p_shell = sub.add_parser("shell", help="Interactive REPL")
    p_shell.set_defaults(_handler="shell")

    # ---- vault ----
    p_vault = sub.add_parser("vault", help="Encrypted chunked vault operations")
    vault_sub = p_vault.add_subparsers(dest="vault_cmd", required=True, metavar="<subcommand>")

    p_vault_upload = vault_sub.add_parser("upload", help="Upload a file as encrypted chunked vault")
    p_vault_upload.add_argument("file", help="Path to file to upload")
    p_vault_upload.add_argument("--to", help="Destination channel (default: vault_main or default_destination)")
    p_vault_upload.add_argument("--encrypt", action="store_true", help="Encrypt with AES-256-GCM")
    p_vault_upload.add_argument("--password", help="Encryption password (or set TGKIT_PASSWORD env var)")
    p_vault_upload.add_argument("--no-compress", action="store_true", help="Skip compression")
    p_vault_upload.add_argument("--description", help="Description message (sent before chunks)")
    p_vault_upload.add_argument("--resume", "-r", action="store_true",
                                help="Resume an interrupted upload (state file <file>.vault_resume.json)")
    p_vault_upload.set_defaults(_handler="vault_upload")

    p_vault_download = vault_sub.add_parser("download", help="Download a vault file")
    p_vault_download.add_argument("link", help="Manifest share link")
    p_vault_download.add_argument("--output", "-o", help="Output file path")
    p_vault_download.add_argument("--output-dir", help="Output directory (filename from manifest)")
    p_vault_download.add_argument("--password", help="Decryption password (or set TGKIT_PASSWORD env var)")
    p_vault_download.add_argument("--resume", "-r", action="store_true",
                                  help="Resume an interrupted download (.downloading temp file is kept)")
    p_vault_download.set_defaults(_handler="vault_download")

    p_vault_info = vault_sub.add_parser("info", help="Show manifest info without downloading")
    p_vault_info.add_argument("link", help="Manifest share link")
    p_vault_info.set_defaults(_handler="vault_info")

    return parser


# ============================================================================
# Dispatcher
# ============================================================================

async def dispatch(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Dispatch to the appropriate command handler."""
    handler = getattr(args, "_handler", None)

    if handler is None:
        console.print("[red]✗[/red] No command specified. Use --help for usage.")
        return 1

    # ---- init ----
    if handler == "init":
        from tgkit.commands.init import cmd_init
        return await cmd_init(args, config_path)

    # ---- status ----
    if handler == "status":
        from tgkit.commands.status import cmd_status
        return await cmd_status(args, config)

    # ---- bot ----
    if handler == "bot_add":
        from tgkit.commands.bots import cmd_bot_add
        return await cmd_bot_add(args, config, config_path)
    if handler == "bot_list":
        from tgkit.commands.bots import cmd_bot_list
        return await cmd_bot_list(args, config)
    if handler == "bot_remove":
        from tgkit.commands.bots import cmd_bot_remove
        return await cmd_bot_remove(args, config, config_path)
    if handler == "bot_test":
        from tgkit.commands.bots import cmd_bot_test
        return await cmd_bot_test(args, config)

    # ---- channel ----
    if handler == "channel_add":
        from tgkit.commands.channels import cmd_channel_add
        return await cmd_channel_add(args, config, config_path)
    if handler == "channel_list":
        from tgkit.commands.channels import cmd_channel_list
        return await cmd_channel_list(args, config)
    if handler == "channel_remove":
        from tgkit.commands.channels import cmd_channel_remove
        return await cmd_channel_remove(args, config)

    # ---- db ----
    if handler == "db_sync":
        from tgkit.commands.db import cmd_db_sync
        return await cmd_db_sync(args, config, config_path)
    if handler == "db_download":
        from tgkit.commands.db import cmd_db_download
        return await cmd_db_download(args, config)
    if handler == "db_find":
        from tgkit.commands.db import cmd_db_find
        return await cmd_db_find(args, config, config_path)
    if handler == "db_status":
        from tgkit.commands.db import cmd_db_status
        return await cmd_db_status(args, config)

    # ---- scan ----
    if handler == "scan_run":
        # --parallel-ranges: use the new ParallelRangeScanner
        if getattr(args, "parallel_ranges", False):
            from tgkit.commands.scan import cmd_scan_parallel
            return await cmd_scan_parallel(args, config, config_path)
        from tgkit.commands.scan import cmd_scan
        # Fill in defaults from config if not specified
        if args.batch_size is None:
            args.batch_size = config.scan.batch_size
        if args.concurrency is None:
            args.concurrency = config.scan.concurrency
        return await cmd_scan(args, config, config_path)
    if handler == "scan_resume":
        from tgkit.commands.scan import cmd_scan_resume
        if args.batch_size is None:
            args.batch_size = config.scan.batch_size
        if args.concurrency is None:
            args.concurrency = config.scan.concurrency
        return await cmd_scan_resume(args, config, config_path)
    if handler == "scan_status":
        from tgkit.commands.scan import cmd_scan_status
        return await cmd_scan_status(args, config)

    # ---- copy / forward / reupload ----
    if handler == "operation":
        from tgkit.commands.ops import cmd_operation
        return await cmd_operation(args, config, config_path)

    # ---- post ----
    if handler == "post_create":
        from tgkit.commands.posts import cmd_post_create
        return await cmd_post_create(args, config, config_path)

    # ---- edit ----
    if handler == "edit":
        from tgkit.commands.edit import cmd_edit
        return await cmd_edit(args, config, config_path)

    # ---- delete ----
    if handler == "delete":
        from tgkit.commands.delete import cmd_delete
        return await cmd_delete(args, config, config_path)

    # ---- tag ----
    if handler == "tag":
        from tgkit.commands.tag import cmd_tag
        return await cmd_tag(args, config, config_path)

    # ---- library ----
    if handler == "library":
        from tgkit.commands.library import cmd_library
        return await cmd_library(args, config)

    # ---- log ----
    if handler == "log":
        from tgkit.commands.log import cmd_log
        return await cmd_log(args, config)

    # ---- stats ----
    if handler == "stats":
        from tgkit.commands.stats import cmd_stats
        return await cmd_stats(args, config)

    # ---- migrate ----
    if handler == "migrate":
        from tgkit.commands.migrate import cmd_migrate
        return await cmd_migrate(args, config, config_path)

    # ---- shell ----
    if handler == "shell":
        from tgkit.commands.shell import cmd_shell
        return await cmd_shell(args, config, config_path)

    # ---- vault ----
    if handler == "vault_upload":
        from tgkit.commands.vault import cmd_vault_upload
        return await cmd_vault_upload(args, config, config_path)
    if handler == "vault_download":
        from tgkit.commands.vault import cmd_vault_download
        return await cmd_vault_download(args, config)
    if handler == "vault_info":
        from tgkit.commands.vault import cmd_vault_info
        return await cmd_vault_info(args, config)

    console.print(f"[red]✗[/red] Unknown handler: {handler}")
    return 1


# ============================================================================
# Main entry point
# ============================================================================

def main() -> None:
    """Main entry point — called by `tgkit` console script."""
    parser = build_parser()
    args = parser.parse_args()

    # Setup logging
    setup_logging("DEBUG" if args.verbose else "INFO")

    # Find config path
    config_path = find_config_path(args.config)

    # Special case: init doesn't need existing config
    if args._handler == "init":
        try:
            asyncio.run(dispatch(args, Config(), config_path))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
            sys.exit(130)
        return

    # Load config for all other commands
    if not config_path.exists():
        console.print(f"[red]✗[/red] Config not found at: {config_path}")
        console.print(f"    Run [bold]tgkit init[/bold] first.")
        sys.exit(1)

    try:
        config = load_config(config_path)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to load config: {e}")
        sys.exit(1)

    # Dispatch
    try:
        exit_code = asyncio.run(dispatch(args, config, config_path))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]✗[/red] Error: {type(e).__name__}: {e}")
        logger.exception("Command failed")
        sys.exit(1)

    sys.exit(exit_code or 0)


if __name__ == "__main__":
    main()
