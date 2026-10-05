"""`tgkit fetch` — download file(s) from message links via MTProto.

Why this command exists
-----------------------
Telegram has TWO ways for bots to download files:

  1. Bot API (api.telegram.org, HTTP):
     - works with just a bot token
     - HARD CAP: 20 MB per file. `getFile` returns
       `400 Bad Request: file is too big` for anything larger.
  2. MTProto (pyrofork, needs api_id + api_hash):
     - up to 2 GB per file
     - this is the transport tgkit uses

Smart routing (the "file 11 is bigger" problem)
------------------------------------------------
An agent that downloaded ten small files fine will hit a wall on file #11
if it is 122 MB. `fetch` makes the right choice automatic:

  • plain file ≤ 2 GB  → downloaded directly via MTProto (always works)
  • plain file > 2 GB  → impossible for any bot; fetch says so clearly
  • vault manifest     → auto-switches to `vault download` (chunked mode)
  • not enough disk    → warns BEFORE downloading, not after

Usage:
    tgkit fetch https://t.me/c/3786156476/46839
    tgkit fetch <link1> <link2> <link3> --out ./downloads   # order preserved
    tgkit fetch <link> --force                               # overwrite existing
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import (
    Progress, SpinnerColumn, TextColumn, BarColumn,
    TaskProgressColumn, TimeRemainingColumn, DownloadColumn,
)

from tgkit.capabilities import detect_capabilities, Capability
from tgkit.config.schema import Config
from tgkit.limits import (
    human_size, download_note, MTPROTO_MAX,
)
from tgkit.models.link import parse_link
from tgkit.transport.bot_pool import AsyncBotPool, start_pool_with_help

logger = logging.getLogger(__name__)
console = Console()

# Media attributes that can carry a filename, in priority order
_MEDIA_ATTRS = ("document", "video", "audio", "voice", "video_note",
                "animation", "sticker")

# Caption prefix identifying a vault manifest message
_MANIFEST_PREFIX = "TGKIT_MANIFEST"


def _media_of(msg: Any) -> Any | None:
    """Return the media object of a message (document/video/photo/...)."""
    for attr in (*_MEDIA_ATTRS, "photo"):
        obj = getattr(msg, attr, None)
        if obj is not None:
            return obj
    return None


def _filename_for(msg: Any, media: Any) -> str:
    """Derive an output filename from the message/media."""
    name = getattr(media, "file_name", None)
    if name:
        return name
    if type(media).__name__ == "Photo":
        return f"photo_{msg.id}.jpg"
    return f"msg_{msg.id}.bin"


def _unique_path(path: Path) -> Path:
    """Never overwrite: foo.zip → foo (1).zip → foo (2).zip ..."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 10_000):
        cand = path.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"cannot find free name for {path}")


def _looks_like_manifest(msg: Any) -> bool:
    """True if the message is a vault manifest (chunked upload pointer)."""
    caption = (getattr(msg, "caption", None) or "")
    if caption.startswith(_MANIFEST_PREFIX):
        return True
    # Some clients see the manifest JSON in the document filename instead
    doc = getattr(msg, "document", None)
    fname = getattr(doc, "file_name", None) or ""
    return fname.startswith(_MANIFEST_PREFIX)


async def _download_plain(bot, msg: Any, media: Any, target: Path,
                          fname: str) -> Path:
    """Download a plain media object with a progress bar. Returns final path."""
    size = getattr(media, "file_size", None) or 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"↓ {fname}", total=size or None)

        def on_progress(current: int, total: int) -> None:
            progress.update(task, completed=current, total=total)

        path = await bot.call(
            lambda: bot.client.raw.download_media(
                msg, file_name=str(target), progress=on_progress
            )
        )
    return Path(path) if path else target


async def fetch_one(
    pool: AsyncBotPool,
    link: str,
    out_dir: Path,
    force: bool,
    config: Config | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    """Fetch a single link. Returns a result dict — never raises."""
    result: dict[str, Any] = {"link": link, "ok": False}

    try:
        channel_id, msg_id = parse_link(link)
    except ValueError as e:
        result["error"] = f"bad link: {e}"
        return result

    bot = await pool.get_next()
    result["bot"] = f"@{bot.username or bot.bot_id}"

    try:
        msg = await bot.call(lambda: bot.client.raw.get_messages(channel_id, msg_id))
    except Exception as e:
        result["error"] = f"get_messages failed: {type(e).__name__}: {e}"
        return result

    if msg is None or getattr(msg, "empty", False):
        result["error"] = (
            f"message {msg_id} not found — deleted, or no access for this bot"
        )
        return result

    # ── Smart routing: vault manifest → switch to vault download ──
    if _looks_like_manifest(msg):
        console.print(
            f"  [cyan]ℹ[/cyan] This message is a [bold]vault manifest[/bold] "
            f"(chunked upload) — switching to vault download automatically."
        )
        if config is None:
            result["error"] = (
                f"vault manifest detected — use: tgkit vault download {link}"
            )
            return result
        try:
            from tgkit.vault.downloader import VaultDownloader
            downloader = VaultDownloader(pool, config)
            dl = await downloader.download(link=link, output_dir=str(out_dir),
                                           password=password)
            result.update(ok=True, output_path=dl["output_path"],
                          size=dl["size"], file=Path(dl["output_path"]).name,
                          mode="vault")
            return result
        except Exception as e:
            hint = ""
            if "password" in str(e).lower():
                hint = (f" — re-run with: tgkit fetch {link} --password <PASSWORD> "
                        f"(or tgkit vault download {link})")
            result["error"] = f"vault download failed: {type(e).__name__}: {e}{hint}"
            return result

    media = _media_of(msg)
    if media is None:
        caption = (getattr(msg, "caption", None) or "")
        if caption.startswith(_MANIFEST_PREFIX):
            result["error"] = (
                f"message {msg_id} is a vault manifest — "
                f"use: tgkit vault download {link}"
            )
        else:
            result["error"] = (
                f"message {msg_id} has no downloadable media "
                f"(text-only?). If it is part of an album, fetch each link "
                f"separately."
            )
        return result

    fname = _filename_for(msg, media)
    size = getattr(media, "file_size", None) or 0
    result["file"] = fname
    result["expected_size"] = size

    # ── Smart routing: size checks BEFORE any transfer ──
    if size > MTPROTO_MAX:
        result["error"] = (
            f"file is {human_size(size)} — over Telegram's 2 GB single-file "
            f"ceiling. No bot can download this message. If the sender used "
            f"tgkit, the original was probably split into a vault — look for "
            f"a TGKIT_MANIFEST message and use `tgkit vault download`."
        )
        return result

    note = download_note(size)
    if note:
        console.print(f"  [yellow]⚠[/yellow] {note}")

    # ── Disk space check (warn before, not after) ──
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        free = shutil.disk_usage(out_dir).free
        if free < size * 1.05:
            result["error"] = (
                f"not enough disk space: need {human_size(size)}, "
                f"only {human_size(free)} free in {out_dir}"
            )
            return result
    except OSError:
        pass  # exotic filesystems — don't block the download

    target = out_dir / fname
    if not force:
        target = _unique_path(target)
    else:
        target.unlink(missing_ok=True)

    try:
        final = await _download_plain(bot, msg, media, target, fname)
        result["ok"] = True
        result["output_path"] = str(final)
        result["size"] = final.stat().st_size
        result["mode"] = "plain"
    except Exception as e:
        result["error"] = f"download failed: {type(e).__name__}: {e}"
        target.unlink(missing_ok=True)

    return result


async def cmd_fetch(args: argparse.Namespace, config: Config) -> int:
    """Download file(s) from t.me message links (plain files, any size ≤ 2 GB)."""
    report = detect_capabilities(config)
    if not report.can(Capability.DOWNLOAD_LARGE):
        console.print(
            "[red]✗[/red] `fetch` requires Tier 2 — set api_id + api_hash in config."
        )
        console.print("    Get them from [cyan]https://my.telegram.org[/cyan] → API Development Tools")
        console.print("    Then verify with: [bold]tgkit doctor[/bold]")
        return 1

    links: list[str] = list(args.links)
    out_dir = Path(args.out).expanduser() if args.out else Path.cwd()
    password = args.password or None
    if not password:
        import os as _os
        password = _os.environ.get("TGKIT_PASSWORD") or None

    console.print("\n[bold]Fetch:[/bold]")
    console.print(f"  Links:    {len(links)} (order preserved)")
    console.print(f"  Out dir:  [cyan]{out_dir}[/cyan]")
    console.print(f"  Mode:     [cyan]MTProto (pyrofork) — no 20 MB Bot API limit[/cyan]")

    pool = AsyncBotPool(
        tokens=config.bot_tokens,
        api_id=config.api.api_id,
        api_hash=config.api.api_hash,
        session_dir=str(config.get_session_dir()),
    )

    console.print(f"\n[bold]Starting {pool.size} bot(s)...[/bold]")
    if not await start_pool_with_help(pool, console):
        return 1

    # Resolve every distinct numeric channel once (64-bit IDs need access_hash)
    numeric_channels: set[int] = set()
    for link in links:
        try:
            ch, _mid = parse_link(link)
            if isinstance(ch, int):
                numeric_channels.add(ch)
        except ValueError:
            pass
    for ch in numeric_channels:
        await pool.resolve_peer_all(ch)

    results: list[dict[str, Any]] = []
    try:
        for link in links:
            console.print(f"\n[bold]→[/bold] {link}")
            r = await fetch_one(pool, link, out_dir, force=args.force,
                                config=config, password=password)
            results.append(r)
            if r["ok"]:
                console.print(
                    f"  [green]✓[/green] {r['file']} — {r['size']:,} bytes "
                    f"→ [bold]{r['output_path']}[/bold]"
                )
            else:
                console.print(f"  [red]✗[/red] {r['error']}")
    finally:
        await pool.stop_all()

    # Final summary (machine-readable for agents)
    ok = sum(1 for r in results if r["ok"])
    console.print(f"\n[bold]Summary:[/bold] {ok}/{len(results)} succeeded")
    for r in results:
        status = "OK " if r["ok"] else "FAIL"
        detail = r.get("output_path") or r.get("error")
        console.print(f"  [{status}] {r['link']} → {detail}")

    return 0 if ok == len(results) else 1
