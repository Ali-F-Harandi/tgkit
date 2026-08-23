"""Scan subsystem — batched + concurrent channel scanning."""

from __future__ import annotations

from tgkit.scan.scanner import BatchedScanner, BatchPolicy
from tgkit.scan.parallel_scanner import (
    ParallelRangeScanner,
    ParallelScanPolicy,
    ParallelScanResult,
    BotSliceResult,
    split_range,
    stable_channel_db_id,
)
from tgkit.scan.extract import extract_message_info, extract_channel_id
from tgkit.scan.state import ScanState

__all__ = [
    "BatchedScanner",
    "BatchPolicy",
    "ParallelRangeScanner",
    "ParallelScanPolicy",
    "ParallelScanResult",
    "BotSliceResult",
    "split_range",
    "stable_channel_db_id",
    "extract_message_info",
    "extract_channel_id",
    "ScanState",
]
