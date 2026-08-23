"""Transport errors: classification + FloodWait helpers."""

from __future__ import annotations

from typing import Any


class TransportError(Exception):
    """Base class for transport-level errors."""


class FloodWaitError(TransportError):
    """Telegram FloodWait — must sleep `retry_after` seconds before retrying."""

    def __init__(self, retry_after: int, message: str = ""):
        self.retry_after = retry_after
        super().__init__(f"FloodWait {retry_after}s: {message}")


def is_flood_wait(exc: BaseException) -> bool:
    """Check if an exception is a FloodWait (works with pyrofork's FloodWait)."""
    # pyrofork raises pyrogram.errors.FloodWait
    exc_type = type(exc).__name__
    if exc_type == "FloodWait" or exc_type.endswith("FloodWait"):
        return True
    # Check class hierarchy
    for cls in type(exc).__mro__:
        if cls.__name__ == "FloodWait":
            return True
    # Some variants have .value and mention "flood" in message
    if hasattr(exc, "value") and "flood" in str(exc).lower():
        return True
    return False


def get_retry_after(exc: BaseException) -> int:
    """Extract retry_after seconds from a FloodWait exception."""
    # pyrofork's FloodWait has .value attribute
    if hasattr(exc, "value"):
        return int(exc.value)
    # Fallback: try to parse from message
    msg = str(exc)
    if "FLOOD_WAIT_" in msg:
        try:
            return int(msg.split("FLOOD_WAIT_")[1].split("_")[0].split(")")[0])
        except (IndexError, ValueError):
            pass
    return 5  # conservative default


def classify_error(exc: BaseException) -> dict[str, Any]:
    """Classify an exception for retry/backoff decisions.

    Returns dict with:
        - kind: 'flood_wait' | 'transient' | 'permanent' | 'unknown'
        - retry_after: int (only for flood_wait)
        - should_retry: bool
        - message: str
    """
    exc_type = type(exc).__name__
    msg = str(exc).lower()

    # FloodWait
    if is_flood_wait(exc):
        return {
            "kind": "flood_wait",
            "retry_after": get_retry_after(exc),
            "should_retry": True,
            "message": str(exc),
        }

    # Transient network errors
    transient_patterns = ["timeout", "connection", "network", "temporarily"]
    if any(p in msg for p in transient_patterns):
        return {
            "kind": "transient",
            "retry_after": 0,
            "should_retry": True,
            "message": str(exc),
        }

    # 5xx server errors (pyrofork wraps these)
    if "500" in msg or "502" in msg or "503" in msg or "internal" in msg:
        return {
            "kind": "transient",
            "retry_after": 0,
            "should_retry": True,
            "message": str(exc),
        }

    # Permanent errors (4xx, auth, etc.)
    permanent_patterns = [
        "unauthorized", "forbidden", "not found", "invalid",
        "chat not found", "peer id invalid", "bot method invalid",
        "message to forward not found", "message to copy not found",
    ]
    if any(p in msg for p in permanent_patterns):
        return {
            "kind": "permanent",
            "retry_after": 0,
            "should_retry": False,
            "message": str(exc),
        }

    # Unknown — be conservative and retry once
    return {
        "kind": "unknown",
        "retry_after": 0,
        "should_retry": True,
        "message": str(exc),
    }
