"""`tgkit send` — upload local file(s) to a channel via MTProto.

Why this command exists
-----------------------
Uploading has the same trap as downloading, just with a different number:
the Bot API (plain HTTP) refuses files over **50 MB** (`Request Entity Too
Large`), while MTProto accepts up to **2 GB**. Without a plain upload
command, agents either misuse `vault upload` (which chunks + manifests —
not what you want for a plain file) or fall back to the Bot API and break
on the first big file.

Smart routing:
    ≤ 50 MB          → uploaded via MTProto (works everywhere)
    50 MB .. 2 GB    → note printed, uploaded via MTProto anyway (fine)
    > 2 GB           → refused with a pointer to `tgkit vault upload`
                       (splits into 19 MB chunks — no total-size limit)

Usage:
    tgkit send report.zip --to @mychannel
    tgkit send a.zip b.zip --to -1003873843444 --caption "Monthly backup"
    tgkit send big_movie.mkv                # uses default_destination from config
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import (
    Progress, SpinnerColumn, TextColumn, BarColumn,
    TaskProgressColumn, TimeRemainingColumn, TransferSpeedColumn,
)

from tgkit.capabilities import detect_capabilities, Capability
from tgkit.config.schema import Config
from tgkit.limits import human_size, upload_note, MTPROTO_MAX
from tgkit.models.link import parse_channel_ref, build_link
from tgkit.transport.bot_pool import AsyncBotPool, start_pool_with_help

logger = logging.getLogger(__name__)
console = Console()


async def _upload_one(bot, dest_channel, file_path: Path, caption: str,
                      fname: str, size: int) -> tuple[bool, int | None, str | None]:
    """Upload a single file with a progress bar. Returns (ok, msg_id, error)."""
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"↑ {fname}", total=size)

            def on_progress(current: int, total: int) -> None:
                progress.update(task, completed=current, total=total)

            msg = await bot.call(
                lambda: bot.client.raw.send_document(
                    chat_id=dest_channel,
                    document=str(file_path),
                    caption=caption or None,
                    disable_notification=True,
                    progress=on_progress,
                )
            )
        return True, msg.id, None
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"


async def cmd_send(args: argparse.Namespace, config: Config) -> int:
    """Upload file(s) to a channel as plain documents (up to 2 GB each)."""
    report = detect_capabilities(config)
    if not report.can(Capability.SEND_FILE):
        console.print("[red]✗[/red] `send` requires at least 1 bot token in config.")
        console.print("    Add one with: [bold]tgkit bot add <TOKEN>[/bold]")
        console.print("    Then verify with: [bold]tgkit doctor[/bold]")
        return 1
    if not report.can(Capability.UPLOAD_LARGE):
        console.print(
            "[red]✗[/red] `send` requires Tier 2 — set api_id + api_hash in config."
        )
        console.print("    Get them from [cyan]https://my.telegram.org[/cyan] → API Development Tools")
        return 1

    # ── Resolve files (validate before touching the network) ──
    files: list[Path] = []
    for raw in args.files:
        p = Path(raw).expanduser()
        if not p.exists():
            console.print(f"[red]✗[/red] File not found: {p}")
            return 1
        if not p.is_file():
            console.print(f"[red]✗[/red] Not a file (directory?): {p}")
            console.print("    Send files one by one — directories are not supported.")
            return 1
        files.append(p)

    # ── Smart routing: size check BEFORE uploading anything ──
    for p in files:
        size = p.stat().st_size
        if size > MTPROTO_MAX:
            note = upload_note(size)
            console.print(f"[red]✗[/red] {p.name} is {human_size(size)}")
            if note:
                console.print(f"    {note}")
            console.print(
                f"    → Use instead: [bold]tgkit vault upload \"{p}\" --to <channel>[/bold]"
            )
            return 1

    # ── Resolve destination ──
    dest = args.to or config.channels.default_destination
    if not dest:
        console.print("[red]✗[/red] No destination. Use --to <channel>, or set default_destination.")
        console.print("    Example: [bold]tgkit send file.zip --to @mychannel[/bold]")
        return 1
    dest_channel = parse_channel_ref(dest) if isinstance(dest, str) else dest

    caption = args.caption or ""

    console.print("\n[bold]Send:[/bold]")
    console.print(f"  Files:     {len(files)}")
    total = sum(p.stat().st_size for p in files)
    console.print(f"  Total:     [cyan]{human_size(total)}[/cyan]")
    console.print(f"  Dest:      [cyan]{dest_channel}[/cyan]")
    console.print(f"  Mode:      [cyan]MTProto (pyrofork) — no 50 MB Bot API limit[/cyan]")

    for p in files:
        note = upload_note(p.stat().st_size)
        if note:
            console.print(f"  [yellow]⚠[/yellow] {p.name}: {note}")

    # ── Start pool ──
    pool = AsyncBotPool(
        tokens=config.bot_tokens,
        api_id=config.api.api_id,
        api_hash=config.api.api_hash,
        session_dir=str(config.get_session_dir()),
    )
    console.print(f"\n[bold]Starting {pool.size} bot(s)...[/bold]")
    if not await start_pool_with_help(pool, console):
        return 1

    if isinstance(dest_channel, int):
        await pool.resolve_peer_all(dest_channel)
        await pool.prime_destination_all(dest_channel)

    # ── Upload sequentially (round-robin bots), order preserved ──
    results: list[dict[str, Any]] = []
    try:
        for p in files:
            size = p.stat().st_size
            bot = await pool.get_for_channel(dest_channel) if isinstance(dest_channel, int) \
                else await pool.get_next()
            console.print(f"\n[bold]→[/bold] {p.name} ({human_size(size)})")
            ok, msg_id, error = await _upload_one(
                bot, dest_channel, p, caption, p.name, size
            )
            if ok:
                share = build_link(dest_channel, msg_id)
                results.append({"file": str(p), "ok": True, "msg_id": msg_id, "link": share})
                console.print(f"  [green]✓[/green] → [bold cyan]{share}[/bold cyan]")
            else:
                results.append({"file": str(p), "ok": False, "error": error})
                console.print(f"  [red]✗[/red] {error}")
                # Channel admin problems are the most common cause — help the user
                if "USER_NOT_PARTICIPANT" in (error or "") or "chat not found" in (error or "").lower() \
                        or "forbidden" in (error or "").lower():
                    console.print(
                        "  [yellow]ℹ[/yellow] The bot may not be an admin in the destination channel."
                    )
                    console.print(
                        "      → Add the bot as an admin with 'Post messages' permission."
                    )
    finally:
        await pool.stop_all()

    ok = sum(1 for r in results if r["ok"])
    console.print(f"\n[bold]Summary:[/bold] {ok}/{len(results)} uploaded")
    for r in results:
        if r["ok"]:
            console.print(f"  [OK ] {r['file']} → {r['link']}")
        else:
            console.print(f"  [FAIL] {r['file']} → {r['error']}")

    return 0 if ok == len(results) else 1
