"""BatchRunner — parallel execution of operations with resume + logging.

Takes a list of MessageInfo items + an Operation + parallel count.
Runs them in parallel via asyncio, streams results to operations_log,
and supports resume (skip items already processed in a previous run).

Usage:
    runner = BatchRunner(op=CopyOp(caption="..."), parallel=5)
    result = await runner.run(items, ctx, on_progress=my_callback)
    print(f"{result.success_count} ok, {result.failure_count} failed")
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from tgkit.operations.base import Operation, OpContext, OpResult
from tgkit.models.message import MessageInfo

logger = logging.getLogger(__name__)


@dataclass
class BatchResult:
    """Result of a batch run."""
    total: int = 0
    success_count: int = 0
    failure_count: int = 0
    results: list[OpResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    effective_rate: float = 0.0  # items per second

    @property
    def is_complete(self) -> bool:
        return self.failure_count == 0

    def summary(self) -> str:
        return (
            f"{self.success_count}/{self.total} ok, "
            f"{self.failure_count} failed, "
            f"{self.effective_rate:.1f} items/s in {self.elapsed_seconds:.1f}s"
        )


class BatchRunner:
    """Run an Operation on a list of items in parallel.

    Attributes:
        op: The Operation to execute
        parallel: Max concurrent operations (default: bot count)
        skip_msg_ids: Set of source msg_ids to skip (for resume)
    """

    def __init__(
        self,
        op: Operation,
        parallel: int = 5,
        skip_msg_ids: set[int] | None = None,
    ):
        self.op = op
        self.parallel = max(1, parallel)
        self.skip_msg_ids = skip_msg_ids or set()

    async def run(
        self,
        items: list[MessageInfo],
        ctx: OpContext,
        on_progress: Callable[[int, int, OpResult], None] | None = None,
    ) -> BatchResult:
        """Run the operation on all items.

        Args:
            items: List of MessageInfo to process
            ctx: OpContext (pool, config, db, dest_channel)
            on_progress: Callback (done_count, total, latest_result)

        Returns:
            BatchResult with all results + stats
        """
        # Filter out skipped items (resume)
        to_process = [
            item for item in items
            if item.ref.msg_id not in self.skip_msg_ids
        ]

        total = len(to_process)
        result = BatchResult(total=total)
        sem = asyncio.Semaphore(self.parallel)
        t0 = time.perf_counter()
        done_count = 0

        logger.info(
            f"BatchRunner: {total} items, parallel={self.parallel}, "
            f"op={self.op.name}"
        )

        async def worker(item: MessageInfo) -> OpResult:
            nonlocal done_count
            async with sem:
                try:
                    res = await self.op.run(item, ctx)
                except Exception as e:
                    res = OpResult(
                        ok=False,
                        error=f"BatchRunner exception: {type(e).__name__}: {e}",
                        source_ref=item.ref,
                    )

                async with result_lock:
                    result.results.append(res)
                    if res.ok:
                        result.success_count += 1
                    else:
                        result.failure_count += 1
                    done_count += 1

                if on_progress:
                    on_progress(done_count, total, res)

                return res

        result_lock = asyncio.Lock()

        # Run all workers in parallel
        tasks = [asyncio.create_task(worker(item)) for item in to_process]
        await asyncio.gather(*tasks)

        # Finalize
        result.elapsed_seconds = time.perf_counter() - t0
        result.effective_rate = (
            result.success_count + result.failure_count
        ) / result.elapsed_seconds if result.elapsed_seconds > 0 else 0

        logger.info(f"BatchRunner complete: {result.summary()}")
        return result
