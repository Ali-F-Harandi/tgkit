"""Operation base — ABC + OpContext + OpResult."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from tgkit.config.schema import Config
from tgkit.transport.bot_pool import AsyncBotPool
from tgkit.db.connection import Database
from tgkit.models.message import MessageRef, MessageInfo
from tgkit.utils import stable_channel_db_id

logger = logging.getLogger(__name__)


@dataclass
class OpContext:
    """Shared context for all operations in a batch.

    Attributes:
        pool: AsyncBotPool (started, ready to use)
        config: tgkit Config
        db: Database (for logging + library)
        dest_channel: Destination channel ID (int or "@username")
        dest_channel_db_id: int form for DB logging
    """
    pool: AsyncBotPool
    config: Config
    db: Database | None
    dest_channel: int | str
    dest_channel_db_id: int


@dataclass
class OpResult:
    """Result of a single operation."""
    ok: bool
    new_msg_id: int | None = None
    share_link: str | None = None
    error: str | None = None
    media_type: str | None = None
    file_name: str | None = None
    file_size: int | None = None
    source_ref: MessageRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "new_msg_id": self.new_msg_id,
            "share_link": self.share_link,
            "error": self.error,
            "media_type": self.media_type,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "source_msg_id": self.source_ref.msg_id if self.source_ref else None,
            "source_channel": self.source_ref.channel_id if self.source_ref else None,
        }


class Operation(ABC):
    """Base class for all operations (forward, copy, reupload).

    Subclasses implement run() which takes a MessageInfo (with source ref + file_id)
    and returns an OpResult.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Operation name (e.g., 'copy', 'forward')."""
        ...

    @abstractmethod
    async def run(self, item: MessageInfo, ctx: OpContext) -> OpResult:
        """Execute the operation on one message.

        Args:
            item: MessageInfo with source ref + file_id + metadata
            ctx: OpContext with pool, config, db, dest_channel

        Returns:
            OpResult
        """
        ...

    async def get_bot(self, ctx: OpContext):
        """Get the next bot from the pool (round-robin)."""
        return await ctx.pool.get_next()

    def log_operation(
        self,
        ctx: OpContext,
        item: MessageInfo,
        result: OpResult,
        bot_id: int | None = None,
    ) -> None:
        """Log the operation to the operations_log table."""
        if ctx.db is None:
            return

        from tgkit.db.store import OperationsLogStore, ChannelStore
        import json

        store = OperationsLogStore(ctx.db)
        source_channel = item.ref.channel_id
        # Convert to int for DB (FK)
        if isinstance(source_channel, int):
            source_db_id = source_channel
        else:
            source_db_id = stable_channel_db_id(source_channel)

        # Ensure source channel exists in DB (for FK constraint)
        ch_store = ChannelStore(ctx.db)
        if not ch_store.get(source_db_id):
            ch_store.upsert(
                channel_id=source_db_id,
                username=source_channel.lstrip("@") if isinstance(source_channel, str) else None,
                role="source",
            )

        store.log(
            op_type=self.name,
            status="ok" if result.ok else "failed",
            source_channel=source_db_id,
            source_msg_id=item.ref.msg_id,
            dest_channel=ctx.dest_channel_db_id if result.ok else None,
            dest_msg_id=result.new_msg_id,
            bot_id=bot_id,
            error=result.error,
            meta_json=json.dumps({
                "file_name": result.file_name,
                "file_size": result.file_size,
                "media_type": result.media_type,
                "share_link": result.share_link,
            }),
        )
