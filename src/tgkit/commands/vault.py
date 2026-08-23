"""`tgkit vault` commands — encrypted chunked upload/download."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

from tgkit.config.schema import Config
from tgkit.models.link import parse_channel_ref
from tgkit.transport.bot_pool import AsyncBotPool
from tgkit.vault.uploader import VaultUploader
from tgkit.vault.downloader import VaultDownloader
from tgkit.db.connection import get_db
from tgkit.db.store import Store
from tgkit.db.sync import DBSync
from tgkit.capabilities import detect_capabilities, Capability

logger = logging.getLogger(__name__)
console = Console()


async def cmd_vault_upload(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Upload a file as encrypted chunked vault."""
    report = detect_capabilities(config)
    if not report.can(Capability.UPLOAD_LARGE):
        console.print("[red]✗[/red] Vault upload requires Tier 2 (api_id + api_hash).")
        return 1

    file_path = Path(args.file)
    if not file_path.exists():
        console.print(f"[red]✗[/red] File not found: {file_path}")
        return 1

    # Resolve destination
    dest = args.to or config.channels.vault_main or config.channels.default_destination
    if not dest:
        console.print("[red]✗[/red] No destination. Use --to, or set vault_main/default_destination.")
        return 1
    dest_channel = parse_channel_ref(dest) if isinstance(dest, str) else dest

    # Encryption
    password = args.password
    if args.encrypt and not password:
        # Check env var
        password = os.environ.get("TGKIT_PASSWORD")
        if not password:
            import getpass
            password = getpass.getpass("Password: ")
    if not args.encrypt:
        password = None

    file_size = file_path.stat().st_size
    chunk_size = config.vault.chunk_size_mb * 1024 * 1024
    total_chunks = max(1, (file_size + chunk_size - 1) // chunk_size)

    console.print(f"\n[bold]Vault upload:[/bold]")
    console.print(f"  File:       [cyan]{file_path.name}[/cyan] ({file_size:,} bytes)")
    console.print(f"  Dest:       [cyan]{dest_channel}[/cyan]")
    console.print(f"  Chunks:     [cyan]{total_chunks}[/cyan] ({config.vault.chunk_size_mb} MB each)")
    console.print(f"  Encrypted:  [cyan]{'yes' if password else 'no'}[/cyan]")
    console.print(f"  Compressed: [cyan]{'yes' if not args.no_compress else 'no'}[/cyan]")

    # Start pool
    pool = AsyncBotPool(
        tokens=config.bot_tokens,
        api_id=config.api.api_id,
        api_hash=config.api.api_hash,
        session_dir=str(config.get_session_dir()),
    )

    console.print(f"\n[bold]Starting {pool.size} bots...[/bold]")
    await pool.start_all()

    if isinstance(dest_channel, int):
        await pool.resolve_peer_all(dest_channel)
        await pool.prime_destination_all(dest_channel)

    try:
        db = get_db(config.get_db_path())
        uploader = VaultUploader(pool, config, db)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Uploading chunks...", total=total_chunks)

            def on_progress(done, total, bytes_uploaded):
                progress.update(task, completed=done)

            result = await uploader.upload(
                file_path=file_path,
                dest_channel=dest_channel,
                encrypt=bool(password),
                password=password,
                compress=not args.no_compress,
                description=args.description or "",
                resume=bool(getattr(args, "resume", False)),
                on_progress=on_progress,
            )

        console.print(f"\n[green]✓[/green] Upload complete!")
        console.print(f"  Share link:    [bold cyan]{result['share_link']}[/bold cyan]")
        console.print(f"  Manifest msg:  {result['manifest_msg_id']}")
        console.print(f"  Chunks:        {result['total_chunks']}")
        console.print(f"  SHA256:        {result['sha256'][:32]}...")
        console.print(f"  Size:          {result['size']:,} bytes")

        # Auto-sync DB
        if config.channels.db_sync and config.db.auto_sync:
            db_sync = DBSync(config, config.get_db_path())
            try:
                msg_id = db_sync.upload(description=f"Vault upload: {file_path.name}")
                if msg_id:
                    console.print(f"[green]✓[/green] DB synced (msg_id={msg_id})")
            finally:
                db_sync.close()

        await pool.stop_all()
        return 0

    except Exception as e:
        console.print(f"\n[red]✗[/red] Error: {type(e).__name__}: {e}")
        logger.exception("Vault upload failed")
        await pool.stop_all()
        return 1


async def cmd_vault_download(args: argparse.Namespace, config: Config) -> int:
    """Download a vault file from a manifest link."""
    report = detect_capabilities(config)
    if not report.can(Capability.DOWNLOAD_LARGE):
        console.print("[red]✗[/red] Vault download requires Tier 2 (api_id + api_hash).")
        return 1

    link = args.link
    password = args.password
    if not password:
        password = os.environ.get("TGKIT_PASSWORD")

    console.print(f"\n[bold]Vault download:[/bold]")
    console.print(f"  Link:     [cyan]{link}[/cyan]")
    if args.output:
        console.print(f"  Output:   [cyan]{args.output}[/cyan]")
    elif args.output_dir:
        console.print(f"  Dir:      [cyan]{args.output_dir}[/cyan]")

    # Start pool
    pool = AsyncBotPool(
        tokens=config.bot_tokens,
        api_id=config.api.api_id,
        api_hash=config.api.api_hash,
        session_dir=str(config.get_session_dir()),
    )

    console.print(f"\n[bold]Starting {pool.size} bots...[/bold]")
    await pool.start_all()

    try:
        downloader = VaultDownloader(pool, config)

        # Fetch manifest first to show info
        manifest = await downloader.fetch_manifest(link)
        if manifest:
            console.print(f"  File:     [cyan]{manifest.name}[/cyan]")
            console.print(f"  Size:     [cyan]{manifest.size:,} bytes[/cyan]")
            console.print(f"  Chunks:   [cyan]{manifest.total_parts}[/cyan]")
            console.print(f"  Encrypted:[cyan]{'yes' if manifest.encrypted else 'no'}[/cyan]")

            if manifest.encrypted and not password:
                import getpass
                password = getpass.getpass("Password: ")
        else:
            console.print(f"[yellow]⚠[/yellow] Could not fetch manifest — attempting download anyway...")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            total = manifest.total_parts if manifest else 1
            task = progress.add_task("Downloading chunks...", total=total)

            def on_progress(done, total_chunks, bytes_downloaded):
                progress.update(task, completed=done)

            result = await downloader.download(
                link=link,
                output_path=args.output,
                output_dir=args.output_dir,
                password=password,
                resume=bool(getattr(args, "resume", False)),
                on_progress=on_progress,
            )

        if result["sha256_verified"]:
            console.print(f"\n[green]✓[/green] Download complete!")
        else:
            console.print(f"\n[red]✗[/red] SHA256 MISMATCH — file may be corrupted!")

        console.print(f"  Output:   [bold]{result['output_path']}[/bold]")
        console.print(f"  Size:     {result['size']:,} bytes")
        console.print(f"  SHA256:   {result['sha256'][:32]}...")

        await pool.stop_all()
        return 0 if result["sha256_verified"] else 1

    except Exception as e:
        console.print(f"\n[red]✗[/red] Error: {type(e).__name__}: {e}")
        logger.exception("Vault download failed")
        await pool.stop_all()
        return 1


async def cmd_vault_info(args: argparse.Namespace, config: Config) -> int:
    """Show manifest info without downloading."""
    link = args.link

    pool = AsyncBotPool(
        tokens=config.bot_tokens,
        api_id=config.api.api_id,
        api_hash=config.api.api_hash,
        session_dir=str(config.get_session_dir()),
    )

    await pool.start_all()

    try:
        downloader = VaultDownloader(pool, config)
        manifest = await downloader.fetch_manifest(link)

        if not manifest:
            console.print(f"[red]✗[/red] Could not fetch manifest from {link}")
            return 1

        console.print(f"[bold]Manifest info:[/bold]")
        console.print(f"  Name:       {manifest.name}")
        console.print(f"  Size:       {manifest.size:,} bytes ({manifest.size / (1024*1024):.1f} MB)")
        console.print(f"  Chunks:     {manifest.total_parts}")
        console.print(f"  Chunk size: {manifest.chunk_size:,} bytes")
        console.print(f"  SHA256:     {manifest.sha256}")
        console.print(f"  Encrypted:  {'yes' if manifest.encrypted else 'no'}")
        console.print(f"  Compressed: {'yes' if manifest.compressed else 'no'}")
        console.print(f"  Date:       {manifest.date}")
        console.print(f"  Session:    {manifest.session_id}")
        if manifest.encrypted:
            console.print(f"  Algorithm:  {manifest.encryption_algorithm}")
            console.print(f"  KDF:        {manifest.encryption_kdf}")

        await pool.stop_all()
        return 0

    except Exception as e:
        console.print(f"[red]✗[/red] Error: {type(e).__name__}: {e}")
        await pool.stop_all()
        return 1
