"""`tgkit doctor` — one-command environment check with fix hints.

Why this command exists
-----------------------
When an agent (or a human) gets tgkit, the classic failure sequence is:

    1. First 10 small files work (Bot API luck or cached session)
    2. File #11 is 122 MB → the lazy path breaks
    3. The agent tries MTProto → pyrofork isn't installed / config is
       missing api_id / a token is revoked / disk is full / no network
    4. Everything collapses with a raw traceback that explains nothing

`tgkit doctor` compresses all of that diagnosis into ONE command that
checks, in order: Python, packages, config, credentials, tokens, network,
sessions, disk — and prints ✓ / ⚠ / ✗ with a HOW-TO-FIX line for every
problem found.

Exit code: 0 = everything critical is fine, 1 = at least one ✗.

Usage:
    tgkit doctor           # fast checks (no MTProto login)
    tgkit doctor --deep    # also logs into Telegram via MTProto (Tier 2 test)
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import platform
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from tgkit import __version__
from tgkit.config.loader import find_config_path, load_config
from tgkit.limits import human_size

logger = logging.getLogger(__name__)
console = Console()

# ── Hard requirement for MTProto speed (pure-python AES is ~20x slower) ──
REQUIRED_PACKAGES = ["pyrogram", "tgcrypto", "cryptography", "rich", "requests"]


class Check:
    """One check result: status ok | warn | fail, detail, fix hint."""

    def __init__(self, name: str, status: str, detail: str = "", fix: str = ""):
        self.name = name
        self.status = status          # "ok" | "warn" | "fail"
        self.detail = detail
        self.fix = fix


_ICON = {"ok": "[green]✓[/green]", "warn": "[yellow]⚠[/yellow]", "fail": "[red]✗[/red]"}


def _print_checks(checks: list[Check], title: str) -> None:
    console.print(f"\n[bold]{title}[/bold]")
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(width=3)   # icon
    table.add_column(width=24)  # name
    table.add_column(ratio=1)   # detail
    for c in checks:
        table.add_row(_ICON[c.status], c.name, c.detail)
    console.print(table)
    for c in checks:
        if c.status != "ok" and c.fix:
            console.print(f"    [bold]Fix:[/bold] {c.fix}")


def check_python() -> Check:
    v = sys.version_info
    if v >= (3, 10):
        return Check("Python", "ok", f"{v.major}.{v.minor}.{v.micro} ({platform.python_implementation()})")
    return Check(
        "Python", "fail", f"{v.major}.{v.minor} — tgkit needs ≥ 3.10",
        fix="Install Python 3.10+ from https://python.org (or: pyenv install 3.12)",
    )


def check_packages() -> list[Check]:
    checks = []
    for pkg in REQUIRED_PACKAGES:
        try:
            mod = importlib.import_module(pkg)
            ver = getattr(mod, "__version__", "?")
            checks.append(Check(f"package: {pkg}", "ok", ver))
        except ImportError:
            fix = (
                "Speed module — install it or uploads/downloads will be very slow: "
                "pip install tgcrypto"
                if pkg == "tgcrypto"
                else f"Required — install everything with: pip install -e ."
            )
            checks.append(Check(f"package: {pkg}", "fail" if pkg != "tgcrypto" else "warn",
                                "not installed", fix=fix))
    return checks


def check_config(config_path: Path | None) -> list[Check]:
    checks: list[Check] = []
    path = config_path if config_path else find_config_path(None)

    if not path or not path.exists():
        return [
            Check(
                "Config file", "fail", "not found",
                fix="Run: tgkit init   (then edit the created config: api_id, api_hash)",
            ),
            Check("api_id / api_hash", "fail", "unknown (no config)", fix="tgkit init"),
            Check("Bot tokens", "fail", "unknown (no config)", fix="tgkit init"),
        ]

    checks.append(Check("Config file", "ok", str(path)))

    try:
        config = load_config(path)
    except Exception as e:
        checks.append(Check("Config file", "fail", f"broken: {e}",
                            fix="Fix the JSON syntax, or recreate it: tgkit init --force "
                                "(⚠ this wipes current settings)"))
        return checks

    # api credentials
    if config.api.api_id and config.api.api_hash:
        checks.append(Check("api_id / api_hash", "ok",
                            f"api_id={config.api.api_id} (hash: {config.api.api_hash[:4]}***)"))
        checks.append(Check("Tier", "ok", "tier2_extended — all commands available"))
    else:
        checks.append(Check(
            "api_id / api_hash", "fail", "missing — Tier 2 disabled (no fetch/send/scan/vault)",
            fix="Create an app at https://my.telegram.org → API Development Tools, "
                "then put api_id + api_hash into the config",
        ))
        checks.append(Check("Tier", "warn", "tier1 only — big files (>20/50 MB) impossible"))

    # bots
    n = len(config.bots)
    if n == 0:
        checks.append(Check("Bot tokens", "fail", "none configured",
                            fix="Add one: tgkit bot add <TOKEN>  (from @BotFather)"))
    else:
        bad = [b for b in config.bots if not _token_looks_ok(b.token)]
        if bad:
            checks.append(Check("Bot tokens", "warn",
                                f"{n} configured, {len(bad)} look malformed",
                                fix="Token format is <digits>:<35 chars> — re-add with: tgkit bot add <TOKEN>"))
        else:
            checks.append(Check("Bot tokens", "ok", f"{n} configured"))
    return checks


def _token_looks_ok(token: str) -> bool:
    parts = token.split(":")
    return len(parts) == 2 and parts[0].isdigit() and len(parts[1]) >= 30


def check_network(config) -> list[Check]:
    """Validate every bot token live via getMe (also proves connectivity)."""
    checks: list[Check] = []
    import requests

    try:
        resp = requests.get("https://api.telegram.org", timeout=10)
        checks.append(Check("Internet", "ok", f"api.telegram.org reachable ({resp.status_code})"))
    except Exception as e:
        return [Check("Internet", "fail", f"cannot reach api.telegram.org: {e}",
                      fix="Check your connection / proxy / firewall (TCP 443)")]

    for i, bot in enumerate(config.bots):
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{bot.token}/getMe", timeout=15
            ).json()
            if r.get("ok"):
                u = r["result"].get("username", "?")
                checks.append(Check(f"bot #{i + 1}", "ok", f"@{u} — token valid"))
            else:
                desc = r.get("description", "unknown error")
                status = "fail"
                fix = "Get a fresh token from @BotFather and re-add: tgkit bot add <TOKEN>"
                if "Too Many Requests" in desc:
                    status, fix = "warn", "FloodWait — token fine, retry in a few seconds"
                checks.append(Check(f"bot #{i + 1}", status, desc, fix=fix))
        except Exception as e:
            checks.append(Check(f"bot #{i + 1}", "fail", f"network error: {e}",
                                fix="Check internet, then re-run: tgkit doctor"))

    return checks


def check_local(config) -> list[Check]:
    """Session dir + disk space."""
    checks: list[Check] = []

    session_dir = config.get_session_dir() if config else Path.home() / ".tgkit/sessions"
    if session_dir.exists():
        sessions = list(session_dir.glob("*.session"))
        checks.append(Check("Session dir", "ok",
                            f"{len(sessions)} session file(s) in {session_dir}"))
    else:
        checks.append(Check("Session dir", "ok",
                            f"{session_dir} (created on first MTProto run)"))

    cwd = Path.cwd()
    try:
        free = shutil.disk_usage(cwd).free
        if free < 500 * 1024 * 1024:
            checks.append(Check("Disk space", "warn",
                                f"only {human_size(free)} free in {cwd}",
                                fix="Free some space — downloads land in the current dir by default"))
        else:
            checks.append(Check("Disk space", "ok", f"{human_size(free)} free in {cwd}"))
    except OSError:
        pass

    return checks


async def _mtproto_deep_test(config) -> Check:
    """Try a real MTProto login with the first bot (validates api_id/hash)."""
    from tgkit.transport.bot_pool import AsyncBotPool

    pool = AsyncBotPool(
        tokens=config.bot_tokens,
        api_id=config.api.api_id,
        api_hash=config.api.api_hash,
        session_dir=str(config.get_session_dir()),
    )
    try:
        await pool.start_all()
        me = await pool.bots[0].client.raw.get_me()
        username = getattr(me, "username", "?")
        return Check("MTProto login (--deep)", "ok",
                     f"connected as @{username} — api_id/api_hash valid")
    except Exception as e:
        name = type(e).__name__
        return Check(
            "MTProto login (--deep)", "fail", f"{name}: {str(e)[:200]}",
            fix="api_id/api_hash wrong → get correct ones at https://my.telegram.org ; "
                "stale session → rm -rf ~/.tgkit/sessions/* and re-run",
        )
    finally:
        try:
            await pool.stop_all()
        except Exception:
            pass


async def cmd_doctor(args: argparse.Namespace, config: Config, config_path: Path | None = None) -> int:
    """Run all environment checks and print a verdict."""
    console.print(f"[bold]tgkit doctor[/bold] — v{__version__} environment check")

    all_checks: list[Check] = []

    # 1. Python + packages
    py = check_python()
    pkgs = check_packages()
    _print_checks([py] + pkgs, "1. Runtime")
    all_checks += [py] + pkgs

    # 2. Config + credentials
    cfg_checks = check_config(config_path)
    _print_checks(cfg_checks, "2. Config & credentials")
    all_checks += cfg_checks

    config_ok = all(c.status != "fail" for c in cfg_checks if c.name in ("Config file", "api_id / api_hash", "Bot tokens"))

    # 3. Network + live token validation (needs valid config)
    net_checks: list[Check] = []
    if config_ok:
        net_checks = check_network(config)
    else:
        net_checks = [Check("Network & tokens", "warn", "skipped — fix config first")]
    _print_checks(net_checks, "3. Network & bot tokens")
    all_checks += net_checks

    # 4. Local: sessions + disk
    local_checks = check_local(config if config_ok else None)
    _print_checks(local_checks, "4. Local environment")
    all_checks += local_checks

    # 5. Optional deep MTProto test
    if args.deep:
        deep: Check
        if config_ok and config.bots:
            deep = await _mtproto_deep_test(config)
        else:
            deep = Check("MTProto login (--deep)", "warn", "skipped — fix config first")
        _print_checks([deep], "5. MTProto deep test")
        all_checks.append(deep)

    # ── Verdict ──
    fails = [c for c in all_checks if c.status == "fail"]
    warns = [c for c in all_checks if c.status == "warn"]

    console.print("\n" + "─" * 60)
    if fails:
        console.print(f"[bold red]✗ {len(fails)} problem(s) found:[/bold red]")
        for c in fails:
            console.print(f"  • {c.name}: {c.detail}")
            if c.fix:
                console.print(f"    → {c.fix}")
        console.print("\nFix the items above, then re-run: [bold]tgkit doctor[/bold]")
        return 1
    if warns:
        console.print(f"[bold yellow]⚠ OK with {len(warns)} warning(s)[/bold yellow] — tgkit will work, "
                      f"but read the notes above.")
        return 0
    console.print("[bold green]✓ All checks passed[/bold green] — environment is ready.")
    console.print("Next: [bold]tgkit fetch https://t.me/c/<channel>/<msg>[/bold]  or  [bold]tgkit send <file> --to @channel[/bold]")
    return 0
