"""Operations subsystem — pluggable verbs for moving messages.

Each Operation is a class with a run() method. They share:
    - OpContext (pool, config, db, dest_channel)
    - OpResult contract
    - BatchRunner for parallelism + resume + logging
"""

from __future__ import annotations

from tgkit.operations.base import Operation, OpContext, OpResult
from tgkit.operations.forward import ForwardOp
from tgkit.operations.copy import CopyOp
from tgkit.operations.reupload import ReuploadOp
from tgkit.operations.batch import BatchRunner, BatchResult

__all__ = [
    "Operation", "OpContext", "OpResult",
    "ForwardOp", "CopyOp", "ReuploadOp",
    "BatchRunner", "BatchResult",
]

# Registry of available operations
OPERATIONS: dict[str, type[Operation]] = {
    "forward": ForwardOp,
    "copy": CopyOp,
    "reupload": ReuploadOp,
}
