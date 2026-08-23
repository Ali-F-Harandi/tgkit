"""ScanState — resume state for interrupted scans.

Stored in the DB (scans table) + a local .state.json file for fast resume.
Tracks:
    - last_completed_id: the next ID to scan (going backwards)
    - found_count / deleted_count
    - status: running | completed | interrupted
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class ScanState:
    """Resume state for a scan.

    Attributes:
        scan_id: DB scan ID (or 0 if not in DB)
        channel_id: Channel being scanned
        start_id: Highest ID to scan (the "last message" link)
        end_id: Lowest ID to scan (usually 1)
        last_completed_id: The last ID that was successfully processed
                          (next scan resumes from last_completed_id - 1)
        found_count: Messages found so far
        deleted_count: Messages that were deleted/inaccessible
        status: running | completed | interrupted
        started_at: Unix timestamp
        finished_at: Unix timestamp (or None)
    """
    scan_id: int = 0
    channel_id: int | str = 0
    start_id: int = 0
    end_id: int = 1
    last_completed_id: int = 0
    found_count: int = 0
    deleted_count: int = 0
    status: str = "running"
    started_at: int = 0
    finished_at: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScanState":
        return cls(
            scan_id=int(d.get("scan_id", 0)),
            channel_id=d.get("channel_id", 0),
            start_id=int(d.get("start_id", 0)),
            end_id=int(d.get("end_id", 1)),
            last_completed_id=int(d.get("last_completed_id", 0)),
            found_count=int(d.get("found_count", 0)),
            deleted_count=int(d.get("deleted_count", 0)),
            status=str(d.get("status", "running")),
            started_at=int(d.get("started_at", 0)),
            finished_at=d.get("finished_at"),
        )

    def save_to_file(self, path: Path) -> None:
        """Save state to a JSON file (for fast resume)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load_from_file(cls, path: Path) -> "ScanState | None":
        """Load state from a JSON file. Returns None if file doesn't exist."""
        if not path.exists():
            return None
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError):
            return None

    @property
    def progress_pct(self) -> float:
        """Progress percentage (0-100)."""
        if self.start_id <= self.end_id:
            return 100.0
        total = self.start_id - self.end_id + 1
        done = self.start_id - self.last_completed_id
        return min(100.0, (done / total) * 100)

    @property
    def remaining(self) -> int:
        """Number of IDs remaining to scan."""
        if self.last_completed_id <= self.end_id:
            return 0
        return self.last_completed_id - self.end_id

    def summary(self) -> str:
        """One-line summary."""
        return (
            f"scan_id={self.scan_id} "
            f"channel={self.channel_id} "
            f"progress={self.progress_pct:.1f}% "
            f"found={self.found_count} "
            f"deleted={self.deleted_count} "
            f"remaining={self.remaining} "
            f"status={self.status}"
        )
