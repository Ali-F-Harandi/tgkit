"""`tgkit scan` commands — scan, resume, status."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

from tgkit.config.schema import Config
from tgkit.config.loader import save_config
from tgkit.models.link import parse_link
from tgkit.transport.bot_pool import AsyncBotPool
from tgkit.scan.scanner import BatchedScanner, BatchPolicy, ScanResult
from tgkit.scan.state import ScanState
from tgkit.db.connection import get_db
from tgkit.db.store import Store, ScanStore, MessageStore
from tgkit.db.sync import DBSync
from tgkit.capabilities import detect_capabilities, Capability
from tgkit.utils import stable_channel_db_id

logger = logging.getLogger(__name__)
console = Console()


# ============================================================================
# tgkit scan <last_msg_link>
# ============================================================================

async def cmd_scan(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Scan a channel backwards from a last message link.

    Usage:
        tgkit scan https://t.me/AWManga/33530
        tgkit scan https://t.me/c/1234567890/1000 --end-id 100
        tgkit scan <link> --output results.json --format json
    """
    # ── Capability check ──
    report = detect_capabilities(config)
    if not report.can(Capability.SCAN_CHANNEL):
        console.print("[red]✗[/red] Scanning requires Tier 2 (api_id + api_hash).")
        console.print("    Set api_id and api_hash in config to unlock.")
        return 1

    if not config.bots:
        console.print("[red]✗[/red] No bots configured.")
        return 1

    # ── Parse the link ──
    try:
        channel_id, start_msg_id = parse_link(args.link)
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        return 1

    end_id = args.end_id

    # ── Continue mode: resume from a checkpoint link ──
    checkpoint_msg_id = None
    checkpoint_channel = None
    preloaded_messages = []  # messages from a previous checkpoint (for resume)

    if args.continue_link:
        # Download existing scan results from the continue link
        console.print(f"[bold]Continue mode:[/bold] resuming from {args.continue_link}")
        try:
            cp_channel, cp_msg_id = parse_link(args.continue_link)
            checkpoint_channel = cp_channel
            checkpoint_msg_id = cp_msg_id

            # Use Bot API to download the checkpoint file
            from tgkit.transport.bot_api import BotAPIClient
            bot_api = BotAPIClient(config.bots[0].token)

            # Forward to same channel to get file_id
            fwd = bot_api.forward_message(cp_channel, cp_channel, cp_msg_id)
            fwd_id = fwd["message_id"]
            doc = fwd.get("document", {})
            if doc:
                file_id = doc["file_id"]
                import tempfile
                temp_path = tempfile.mktemp(suffix=".json")
                bot_api.download_file(file_id, temp_path)
                bot_api.delete_message(cp_channel, fwd_id)

                # Parse the JSON
                preloaded_messages = json.loads(open(temp_path, "r", encoding="utf-8").read())
                os.unlink(temp_path)

                if preloaded_messages:
                    # Find the lowest msg_id = last_completed_id
                    min_msg_id = min(int(m["msg_id"]) for m in preloaded_messages)
                    console.print(f"  [green]✓[/green] Loaded {len(preloaded_messages)} messages from checkpoint")
                    console.print(f"  Last scanned msg_id: {min_msg_id}")
                    console.print(f"  Resuming from msg_id: {min_msg_id - 1}")

                    # Override start_msg_id to resume from where we left off
                    start_msg_id = min_msg_id - 1
                    channel_id = cp_channel  # use the checkpoint's channel
                else:
                    console.print(f"  [yellow]⚠[/yellow] Checkpoint file is empty — starting fresh")
            else:
                console.print(f"  [red]✗[/red] Checkpoint message has no document")
                bot_api.delete_message(cp_channel, fwd_id)
                return 1

            bot_api.close()

        except Exception as e:
            console.print(f"[red]✗[/red] Failed to load checkpoint: {e}")
            return 1

    # ── Continue mode: upload checkpoints to db_sync channel ──
    use_continue = getattr(args, 'continue', False) or args.continue_link is not None
    if use_continue and not checkpoint_channel:
        # First run with --continue: use db_sync channel
        checkpoint_channel = config.channels.db_sync
        if not checkpoint_channel:
            console.print("[red]✗[/red] --continue requires a db_sync channel. Run: tgkit channel add <ID> --role db_sync")
            return 1

    console.print(f"[bold]Scanning channel:[/bold] {channel_id}")
    console.print(f"  From msg_id: [cyan]{start_msg_id}[/cyan] (down to {end_id})")
    console.print(f"  Total IDs to check: {start_msg_id - end_id + 1}")
    if use_continue:
        interval = args.checkpoint_interval or config.scan.checkpoint_interval
        console.print(f"  Continue mode: [cyan]ON[/cyan] (checkpoint every {interval} msgs)")
        if checkpoint_msg_id:
            console.print(f"  Checkpoint msg: [cyan]{checkpoint_msg_id}[/cyan] (will update in-place)")

    # ── Open DB + store ──
    db = get_db(config.get_db_path())
    store = Store(db)

    # ── Ensure channel exists in DB (for FK constraint) ──
    # Convert channel_id to int for DB (use hash for username-based channels)
    channel_db_id = stable_channel_db_id(channel_id)
    existing_ch = store.channels.get(channel_db_id)
    if not existing_ch:
        # Insert a minimal channel record
        store.channels.upsert(
            channel_id=channel_db_id,
            username=channel_id.lstrip("@") if isinstance(channel_id, str) else None,
            role="source",
        )

    # ── Check for existing scan to resume ──
    existing_scan = None
    if not args.fresh:
        # Look for an interrupted scan on this channel
        recent_scans = store.scans.list_recent(limit=20)
        for s in recent_scans:
            if (s["channel_id"] == channel_id or str(s["channel_id"]) == str(channel_id)):
                if s["status"] == "interrupted":
                    existing_scan = s
                    break

    # ── Create or resume scan ──
    resume_state = None
    if existing_scan:
        scan_id = existing_scan["id"]
        resume_state = ScanState(
            scan_id=scan_id,
            channel_id=existing_scan["channel_id"],
            start_id=existing_scan["start_id"],
            end_id=existing_scan["end_id"],
            last_completed_id=existing_scan["last_completed_id"] or (existing_scan["start_id"] + 1),
            found_count=existing_scan["found_count"],
            deleted_count=existing_scan["deleted_count"],
            status="running",
            started_at=existing_scan["started_at"],
        )
        resume_from = resume_state.last_completed_id - 1
        if resume_from < existing_scan["end_id"]:
            console.print(f"\n[yellow]⚠[/yellow] Scan #{scan_id} was already complete (no messages to resume).")
            return 0
        console.print(f"\n[green]↻[/green] Resuming scan #{scan_id} from msg_id={resume_from}")
        console.print(f"  Already found: {resume_state.found_count}, deleted: {resume_state.deleted_count}")
    else:
        # Create new scan record
        scan_id = store.scans.create(
            channel_id=channel_db_id,
            start_id=start_msg_id,
            end_id=end_id,
            config_json=json.dumps({
                "batch_size": args.batch_size,
                "concurrency": args.concurrency,
            }),
        )
        console.print(f"\n[green]✓[/green] Created scan #{scan_id}")

    # ── Build batch policy ──
    policy = BatchPolicy(
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        replies=0,
        save_every_n_batches=config.scan.save_every_n_batches,
    )

    # ── Progress tracking ──
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("({task.completed}/{task.total})"),
        TimeRemainingColumn(),
        console=console,
    )

    total_ids = (resume_state.last_completed_id - 1 - end_id + 1) if resume_state else (start_msg_id - end_id + 1)
    if total_ids <= 0:
        total_ids = 1

    # ── Batch callback: insert into DB ──
    messages_inserted = 0
    all_scanned_messages = list(preloaded_messages)  # for checkpoint uploads

    def on_batch(messages: list) -> None:
        nonlocal messages_inserted, all_scanned_messages
        if messages:
            # Convert MessageInfo to dict for DB insert
            msg_dicts = [m.to_dict() for m in messages]
            count = store.messages.insert_batch(
                scan_id=scan_id,
                channel_id=channel_db_id,
                messages=msg_dicts,
            )
            messages_inserted += count
            # Also collect for checkpoint uploads
            all_scanned_messages.extend(msg_dicts)

    # ── Checkpoint callback: upload/update scan results to channel ──
    from tgkit.transport.bot_api import BotAPIClient
    bot_api_for_checkpoint = BotAPIClient(config.bots[0].token) if use_continue else None

    def on_checkpoint(state: ScanState, messages_snapshot: list) -> None:
        """Upload or update the scan results JSON to the checkpoint channel."""
        nonlocal checkpoint_msg_id
        if not use_continue or not bot_api_for_checkpoint:
            return

        try:
            # Serialize all messages to JSON
            all_dicts = [m.to_dict() for m in messages_snapshot]
            # Also include preloaded messages from a previous checkpoint
            if preloaded_messages and len(all_scanned_messages) > len(all_dicts):
                all_dicts = all_scanned_messages  # use the full list including preloaded

            json_data = json.dumps(all_dicts, ensure_ascii=False)
            caption = (
                f"📊 Scan checkpoint: {channel_id}\n"
                f"Found: {state.found_count + len(preloaded_messages)}\n"
                f"Last msg_id: {state.last_completed_id}\n"
                f"Status: {state.status}"
            )

            # Write to temp file
            import tempfile
            temp_path = tempfile.mktemp(suffix=".json")
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(json_data)

            if checkpoint_msg_id:
                # Update existing checkpoint message (editMessageMedia)
                try:
                    bot_api_for_checkpoint.edit_message_media(
                        chat_id=checkpoint_channel,
                        message_id=checkpoint_msg_id,
                        media_path=temp_path,
                        media_type="document",
                    )
                    # Also update caption
                    try:
                        bot_api_for_checkpoint.edit_message_caption(
                            chat_id=checkpoint_channel,
                            message_id=checkpoint_msg_id,
                            caption=caption,
                        )
                    except Exception:
                        pass
                    console.print(f"  [dim]Checkpoint updated (msg_id={checkpoint_msg_id}, {len(all_dicts)} msgs)[/dim]")
                except Exception:
                    # If edit fails, upload new
                    result = bot_api_for_checkpoint.send_document(
                        chat_id=checkpoint_channel,
                        document_path=temp_path,
                        caption=caption,
                    )
                    # Delete old checkpoint
                    if checkpoint_msg_id:
                        try:
                            bot_api_for_checkpoint.delete_message(checkpoint_channel, checkpoint_msg_id)
                        except Exception:
                            pass
                    checkpoint_msg_id = result["message_id"]
                    console.print(f"  [dim]Checkpoint re-uploaded (msg_id={checkpoint_msg_id})[/dim]")
            else:
                # First checkpoint: upload new
                result = bot_api_for_checkpoint.send_document(
                    chat_id=checkpoint_channel,
                    document_path=temp_path,
                    caption=caption,
                )
                checkpoint_msg_id = result["message_id"]
                console.print(f"  [green]✓[/green] Checkpoint uploaded (msg_id={checkpoint_msg_id})")
                console.print(f"    Continue link: https://t.me/c/{str(checkpoint_channel)[4:]}/{checkpoint_msg_id}" if isinstance(checkpoint_channel, int) else f"    Continue link: https://t.me/{str(checkpoint_channel).lstrip('@')}/{checkpoint_msg_id}")

            os.unlink(temp_path)

        except Exception as e:
            console.print(f"  [yellow]⚠[/yellow] Checkpoint failed: {e}")

    def on_progress(state: ScanState) -> None:
        # Update DB scan record
        store.scans.update_progress(
            scan_id=scan_id,
            found_count=state.found_count,
            deleted_count=state.deleted_count,
            last_completed_id=state.last_completed_id,
        )
        # Log progress
        elapsed = time.time() - state.started_at
        rate = (state.found_count + state.deleted_count) / elapsed if elapsed > 0 else 0
        remaining = state.remaining
        eta = remaining / rate if rate > 0 else 0
        console.print(
            f"  [{state.found_count + state.deleted_count}/{total_ids}] "
            f"found={state.found_count} deleted={state.deleted_count}  "
            f"{rate:.1f} msg/s, ETA {eta:.0f}s",
            style="dim",
        )

    # ── Run the scan ──
    try:
        # Start bot pool
        pool = AsyncBotPool(
            tokens=config.bot_tokens,
            api_id=config.api.api_id,
            api_hash=config.api.api_hash,
            session_dir=str(config.get_session_dir()),
        )

        console.print(f"\n[bold]Starting {pool.size} bots...[/bold]")
        await pool.start_all()

        # Resolve peer on ALL bots (both username and numeric IDs).
        # For username channels: get_chat caches the peer.
        # For numeric IDs: channels.GetChannels caches access_hash.
        # This prevents "PERSISTENT_TIMESTAMP_OUTDATED" (error_code 48)
        # when multiple bots try to access a channel only one has resolved.
        for bot in pool.bots:
            try:
                await bot.client.raw.get_chat(channel_id)
            except Exception:
                pass

        # Run scanner
        scanner = BatchedScanner(pool, config, policy)

        with progress:
            task = progress.add_task("Scanning...", total=total_ids)

            # Wrap on_progress to also update the progress bar
            def on_progress_with_bar(state: ScanState) -> None:
                on_progress(state)
                done = state.found_count + state.deleted_count
                progress.update(task, completed=done)

            result = await scanner.scan(
                channel=channel_id,
                start_id=start_msg_id,
                end_id=end_id,
                scan_id=scan_id,
                resume_state=resume_state,
                on_progress=on_progress_with_bar,
                on_batch=on_batch,
                on_checkpoint=on_checkpoint if use_continue else None,
                checkpoint_interval=(args.checkpoint_interval or config.scan.checkpoint_interval) if use_continue else 0,
            )

        # Mark scan complete in DB
        store.scans.complete(
            scan_id=scan_id,
            found_count=result.state.found_count,
            deleted_count=result.state.deleted_count,
        )

        # Final checkpoint: upload the complete results
        if use_continue and bot_api_for_checkpoint:
            final_msgs = [m.to_dict() for m in result.messages]
            if preloaded_messages:
                final_msgs = preloaded_messages + final_msgs
            on_checkpoint(result.state, result.messages)
            if bot_api_for_checkpoint:
                bot_api_for_checkpoint.close()

        # Print summary
        console.print()
        console.print(f"[green]✓[/green] Scan complete!")
        console.print(f"  Found:    [bold]{result.state.found_count + len(preloaded_messages)}[/bold] messages")
        console.print(f"  Deleted:  {result.state.deleted_count} messages")
        console.print(f"  Time:     {result.elapsed_seconds:.1f}s")
        console.print(f"  Rate:     {result.effective_rate:.1f} msg/s")

        # Print continue link if in continue mode
        if use_continue and checkpoint_msg_id:
            from tgkit.models.link import build_link
            cp_link = build_link(checkpoint_channel, checkpoint_msg_id)
            console.print(f"\n  [bold green]Continue link:[/bold green] {cp_link}")
            console.print(f"  [dim]Use this link to resume if interrupted:[/dim]")
            console.print(f"  [dim]  tgkit scan run <original_link> --continue-link {cp_link}[/dim]")

        # Bot stats
        if result.bot_stats:
            console.print()
            bot_table = Table(title="Bot Stats", show_header=True)
            bot_table.add_column("Bot", style="cyan")
            bot_table.add_column("Requests", justify="right")
            bot_table.add_column("FloodWaits", justify="right")
            bot_table.add_column("Errors", justify="right")
            bot_table.add_column("Batch Size", justify="right")
            for bs in result.bot_stats:
                bot_table.add_row(
                    f"@{bs['username']}",
                    str(bs["request_count"]),
                    str(bs["floodwait_count"]),
                    str(bs["error_count"]),
                    str(bs["final_batch_size"]),
                )
            console.print(bot_table)

        # ── Output file (JSON/CSV) ──
        if args.output:
            output_path = Path(args.output)
            fmt = args.format or output_path.suffix.lstrip(".") or "json"

            if fmt == "json":
                output_path.write_text(
                    json.dumps(
                        [m.to_dict() for m in result.messages],
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            elif fmt == "csv":
                _write_csv(output_path, result.messages)
            else:
                console.print(f"[red]✗[/red] Unknown format: {fmt}")
                return 1

            console.print(f"\n[green]✓[/green] Output saved: [bold]{output_path}[/bold]")
            console.print(f"  Size: {output_path.stat().st_size:,} bytes")

        # ── Auto-sync DB to channel ──
        if config.channels.db_sync and config.db.auto_sync:
            console.print(f"\n[bold]Auto-syncing DB to channel...[/bold]")
            db_sync = DBSync(config, config.get_db_path())
            try:
                msg_id = db_sync.upload(description=f"Scan #{scan_id} complete")
                if msg_id:
                    console.print(f"[green]✓[/green] DB synced (msg_id={msg_id})")
            finally:
                db_sync.close()

        await pool.stop_all()
        return 0

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠[/yellow] Interrupted — saving state...")
        store.scans.mark_interrupted(scan_id)
        console.print(f"[green]✓[/green] Run same command to resume.")
        return 130

    except Exception as e:
        console.print(f"\n[red]✗[/red] Error: {type(e).__name__}: {e}")
        logger.exception("Scan failed")
        store.scans.mark_interrupted(scan_id)
        return 1


# ============================================================================
# tgkit scan resume <scan_id>
# ============================================================================

async def cmd_scan_resume(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Resume an interrupted scan by scan_id."""
    scan_id = args.scan_id
    db = get_db(config.get_db_path())
    store = Store(db)

    scan = store.scans.get(scan_id)
    if not scan:
        console.print(f"[red]✗[/red] Scan #{scan_id} not found.")
        return 1

    if scan["status"] == "completed":
        console.print(f"[yellow]⚠[/yellow] Scan #{scan_id} is already completed.")
        return 0

    console.print(f"[bold]Resuming scan #{scan_id}[/bold]")
    console.print(f"  Channel: {scan['channel_id']}")
    console.print(f"  Range: {scan['start_id']} → {scan['end_id']}")
    console.print(f"  Last completed: {scan['last_completed_id']}")
    console.print(f"  Found so far: {scan['found_count']}")

    # Re-run scan with the same parameters
    # Build a link from the channel_id + start_id
    channel_id = scan["channel_id"]
    if isinstance(channel_id, int) and str(channel_id).startswith("-100"):
        link = f"https://t.me/c/{str(channel_id)[4:]}/{scan['start_id']}"
    else:
        link = f"https://t.me/{str(channel_id).lstrip('@')}/{scan['start_id']}"

    # Call cmd_scan with the link
    args.link = link
    args.end_id = scan["end_id"]
    args.fresh = False
    return await cmd_scan(args, config, config_path)


# ============================================================================
# tgkit scan status [scan_id]
# ============================================================================

async def cmd_scan_status(args: argparse.Namespace, config: Config) -> int:
    """Show scan status."""
    db = get_db(config.get_db_path())
    store = Store(db)

    if args.scan_id:
        scan = store.scans.get(args.scan_id)
        if not scan:
            console.print(f"[red]✗[/red] Scan #{args.scan_id} not found.")
            return 1
        scans = [scan]
    else:
        scans = store.scans.list_recent(limit=10)

    if not scans:
        console.print("[yellow]No scans found.[/yellow]")
        return 0

    table = Table(title="Recent Scans", show_header=True)
    table.add_column("ID", style="cyan", width=5)
    table.add_column("Channel", style="green", width=20)
    table.add_column("Range", width=20)
    table.add_column("Found", justify="right", width=8)
    table.add_column("Deleted", justify="right", width=8)
    table.add_column("Status", style="yellow", width=12)
    table.add_column("Started", style="dim", width=20)

    from datetime import datetime
    for s in scans:
        ch = str(s["channel_id"])
        range_str = f"{s['start_id']}→{s['end_id']}"
        started = datetime.fromtimestamp(s["started_at"]).strftime("%m-%d %H:%M")
        status = s["status"]
        table.add_row(
            str(s["id"]),
            ch[:18],
            range_str,
            str(s["found_count"]),
            str(s["deleted_count"]),
            status,
            started,
        )

    console.print(table)
    return 0


# ============================================================================
# tgkit scan run <link> --parallel-ranges  (ParallelRangeScanner)
# ============================================================================

async def cmd_scan_parallel(args: argparse.Namespace, config: Config, config_path: Path) -> int:
    """Scan a channel using ParallelRangeScanner — N bots, N disjoint ID ranges.

    Each bot gets its own contiguous slice of the ID range. Bots run concurrently
    via asyncio.gather, so FloodWaits overlap in wall-clock time. Per-bot resume
    state is persisted in bot_scan_state, so re-running the same scan resumes
    from where each bot left off (bots that already finished are skipped).

    Throughput: ~5x faster than the default BatchedScanner on the same channel.
    """
    from tgkit.scan.parallel_scanner import (
        ParallelRangeScanner, ParallelScanPolicy,
    )
    from tgkit.transport.bot_pool import AsyncBotPool
    from tgkit.transport.throttle import ThrottlePolicy
    from tgkit.capabilities import detect_capabilities, Capability

    # ── Capability check ──
    report = detect_capabilities(config)
    if not report.can(Capability.SCAN_CHANNEL):
        console.print("[red]✗[/red] Scanning requires Tier 2 (api_id + api_hash).")
        return 1
    if not config.bots:
        console.print("[red]✗[/red] No bots configured.")
        return 1

    # ── Parse the link ──
    try:
        channel_id, start_msg_id = parse_link(args.link)
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        return 1

    end_id = args.end_id
    total_ids = start_msg_id - end_id + 1

    console.print(f"[bold]ParallelRangeScanner[/bold] — scan {channel_id}")
    console.print(f"  Range: [cyan]{start_msg_id}[/cyan] → {end_id}  ({total_ids} IDs)")
    console.print(f"  Bots:  [bold]{len(config.bots)}[/bold] (each gets its own slice)")

    # ── Open DB + store ──
    db = get_db(config.get_db_path())
    store = Store(db)

    # Convert channel_id to int for DB
    # Use a stable hash (Python's hash() is randomized per-process, so use hashlib)
    from tgkit.scan.parallel_scanner import stable_channel_db_id
    channel_db_id = stable_channel_db_id(channel_id)
    if not store.channels.get(channel_db_id):
        store.channels.upsert(
            channel_id=channel_db_id,
            username=channel_id.lstrip("@") if isinstance(channel_id, str) else None,
            role="source",
        )

    # ── Find an existing interrupted scan to resume, else create new ──
    # Logic:
    #   1. Scan the recent scans for this channel.
    #   2. If any has bot_scan_state rows AND all bots are 'completed' → say "already done, use --fresh"
    #   3. Else if any has bot_scan_state rows AND status is interrupted/running → resume it
    #   4. Else → create a new scan
    scan_id = None
    if not args.fresh:
        for s in store.scans.list_recent(limit=20):
            if not (s["channel_id"] == channel_db_id or str(s["channel_id"]) == str(channel_db_id)):
                continue

            bot_states = store.bot_scan_state.list_for_scan(s["id"])
            if not bot_states:
                # Not a parallel scan — skip
                continue

            all_bots_done = all(b["status"] == "completed" for b in bot_states)

            if all_bots_done:
                console.print(
                    f"[yellow]ℹ[/yellow] Scan #{s['id']} on this channel is already "
                    f"completed (found={s['found_count']}, deleted={s['deleted_count']})."
                )
                console.print(
                    f"    Use [bold]--fresh[/bold] to start a new scan, "
                    f"or query results with [bold]tgkit library[/bold]."
                )
                return 0

            if s["status"] in ("interrupted", "running"):
                scan_id = s["id"]
                completed = sum(1 for b in bot_states if b["status"] == "completed")
                console.print(
                    f"\n[green]↻[/green] Resuming parallel scan #{scan_id} "
                    f"({completed}/{len(bot_states)} bots already completed)"
                )
                for b in bot_states:
                    console.print(
                        f"    bot{b['bot_idx']} @{b['bot_username']:18s} "
                        f"range {b['range_start']}→{b['range_end']} "
                        f"status={b['status']} found={b['found_count']}"
                    )
                break

    if scan_id is None:
        scan_id = store.scans.create(
            channel_id=channel_db_id,
            start_id=start_msg_id,
            end_id=end_id,
            config_json=json.dumps({
                "mode": "parallel_ranges",
                "batch_size": args.batch_size or 100,
                "inter_batch_sleep": args.inter_batch_sleep or 0.3,
                "bots": [b.username for b in config.bots],
            }),
        )
        console.print(f"\n[green]✓[/green] Created scan #{scan_id}")

    # ── Build policy ──
    policy = ParallelScanPolicy(
        batch_size=args.batch_size or 100,
        inter_batch_sleep=args.inter_batch_sleep if args.inter_batch_sleep is not None else 0.3,
    )

    # ── Build bot pool with staggered startup (avoid auth FloodWait burst) ──
    # Use a permissive throttle since each bot hits the channel at most once
    # every ~0.3s + FloodWait; the per-bot TokenBucket-style behavior comes
    # from inter_batch_sleep + FloodWait handling inside ParallelRangeScanner.
    pool = AsyncBotPool(
        tokens=config.bot_tokens,
        api_id=config.api.api_id,
        api_hash=config.api.api_hash,
        session_dir=str(config.get_session_dir()),
        throttle=ThrottlePolicy(
            min_interval=0.05,        # 20 RPC/s floor (per bot)
            max_interval=5.0,
            backoff_factor=1.5,
            decay_factor=0.9,
            decay_every=10,
            current_delay=0.05,
        ),
    )

    console.print(f"\n[bold]Starting {pool.size} bots (staggered)...[/bold]")
    t0 = time.time()
    await pool.start_all()
    console.print(f"[green]✓[/green] All {pool.size} bots started in {time.time()-t0:.1f}s")

    # Pre-resolve peer on ALL bots (avoid per-bot peer resolution races)
    for bot in pool.bots:
        try:
            await bot.client.raw.get_chat(channel_id)
        except Exception:
            pass

    # ── on_batch: insert into DB ──
    def on_batch(messages: list) -> None:
        if messages:
            msg_dicts = [m.to_dict() for m in messages]
            store.messages.insert_batch(
                scan_id=scan_id,
                channel_id=channel_db_id,
                messages=msg_dicts,
            )

    # ── Progress display ──
    last_progress_print = [0.0]
    def on_progress(progress: dict[int, dict]) -> None:
        now = time.time()
        if now - last_progress_print[0] < 15.0:
            return
        last_progress_print[0] = now
        lines = []
        total_found = 0
        total_deleted = 0
        total_total = 0
        for bot_idx in sorted(progress.keys()):
            p = progress[bot_idx]
            total = p["range_start"] - p["range_end"] + 1
            done = p["found"] + p["deleted"]
            pct = (done / total * 100) if total > 0 else 0
            total_found += p["found"]
            total_deleted += p["deleted"]
            total_total += total
            if p["status"] == "running":
                elapsed = time.time() - p["started_at"]
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                lines.append(
                    f"  bot{bot_idx} @{p['username']:18s} "
                    f"[{p['range_start']:>5}→{p['range_end']:>5}]: "
                    f"{done:>5}/{total} ({pct:4.0f}%)  "
                    f"f={p['found']:>5}  {rate:5.1f}/s  eta={eta:>4.0f}s"
                )
            elif p["status"] == "completed":
                lines.append(
                    f"  bot{bot_idx} @{p['username']:18s} "
                    f"[{p['range_start']:>5}→{p['range_end']:>5}]: "
                    f"✓ DONE f={p['found']:>5}"
                )
        if lines:
            total_done = total_found + total_deleted
            total_pct = (total_done / total_total * 100) if total_total > 0 else 0
            console.print(
                f"[dim]PROGRESS ({total_done}/{total_total} = {total_pct:.1f}%):[/dim]\n"
                + "\n".join(lines),
                style="dim",
            )

    # ── Run the parallel scan ──
    scanner = ParallelRangeScanner(pool, config, store, policy)
    try:
        result = await scanner.scan(
            channel=channel_id,
            start_id=start_msg_id,
            end_id=end_id,
            scan_id=scan_id,
            on_progress=on_progress,
            on_batch=on_batch,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠[/yellow] Interrupted — saving state for resume...")
        store.scans.mark_interrupted(scan_id)
        console.print(f"[green]✓[/green] Re-run the same command to resume.")
        await pool.stop_all()
        return 130
    except Exception as e:
        console.print(f"\n[red]✗[/red] Error: {type(e).__name__}: {e}")
        logger.exception("Parallel scan failed")
        store.scans.mark_interrupted(scan_id)
        await pool.stop_all()
        return 1

    # ── Print summary ──
    console.print()
    console.print(f"[green]✓[/green] Parallel scan complete!")
    console.print(f"  Total found:   [bold]{result.total_found}[/bold]")
    console.print(f"  Total deleted: {result.total_deleted}")
    console.print(f"  Time:          {result.elapsed_seconds:.1f}s")
    console.print(f"  Aggregate rate:[bold green] {result.aggregate_rate:.1f} msg/s[/bold green]")

    # Per-bot table
    if result.slices:
        console.print()
        bot_table = Table(title="Per-bot Results", show_header=True)
        bot_table.add_column("Bot", style="cyan")
        bot_table.add_column("Range", width=15)
        bot_table.add_column("Found", justify="right", style="green")
        bot_table.add_column("Deleted", justify="right", style="dim")
        bot_table.add_column("Time", justify="right")
        bot_table.add_column("Rate", justify="right")
        bot_table.add_column("Resumed?", justify="center")
        for s in result.slices:
            bot_table.add_row(
                f"@{s.username}",
                f"{s.range_start}→{s.range_end}",
                str(s.found),
                str(s.deleted),
                f"{s.elapsed_seconds:.1f}s",
                f"{s.rate:.1f}/s",
                "↻" if s.resumed else "✨",
            )
        console.print(bot_table)

    # ── Output file ──
    if args.output:
        output_path = Path(args.output)
        fmt = args.format or output_path.suffix.lstrip(".") or "json"
        # Read all messages from DB (sorted by msg_id)
        all_msgs = store.messages.query_by_scan(scan_id, limit=None)
        if fmt == "json":
            output_path.write_text(
                json.dumps(all_msgs, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        elif fmt == "csv":
            # Reuse existing CSV helper
            from tgkit.scan.scanner import ScanResult
            from tgkit.scan.state import ScanState
            # Build pseudo MessageInfo list — simpler to use raw dicts
            _write_csv_from_dicts(output_path, all_msgs)
        console.print(f"\n[green]✓[/green] Output saved: [bold]{output_path}[/bold]")
        console.print(f"  Size: {output_path.stat().st_size:,} bytes")

    await pool.stop_all()
    return 0


def _write_csv_from_dicts(path: Path, messages: list[dict]) -> None:
    """Write message dicts (from DB) to CSV."""
    if not messages:
        path.write_text("", encoding="utf-8")
        return
    columns = [
        "msg_id", "date", "media_type", "file_name",
        "file_extension", "file_size", "mime_type",
        "duration", "width", "height", "caption",
        "file_id",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, quoting=csv.QUOTE_MINIMAL, extrasaction="ignore")
        writer.writeheader()
        for m in messages:
            writer.writerow(m)


# ============================================================================
# Helpers
# ============================================================================

def _write_csv(path: Path, messages: list) -> None:
    """Write messages to CSV (18 columns)."""
    if not messages:
        path.write_text("", encoding="utf-8")
        return

    # Use the 18-column schema
    columns = [
        "msg_id", "message_link", "date", "media_type",
        "file_name", "file_extension", "file_size", "file_size_mb",
        "mime_type", "duration", "width", "height",
        "caption", "caption_length", "is_marker", "marker_emoji",
        "has_thumb", "file_id",
    ]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for msg in messages:
            writer.writerow(msg.to_dict())
