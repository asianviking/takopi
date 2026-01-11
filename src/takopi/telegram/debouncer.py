"""Message debouncer for batching rapid-fire messages by topic."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..context import RunContext
from ..model import EngineId, ResumeToken


@dataclass(slots=True)
class PendingMessage:
    """A message waiting to be batched."""

    chat_id: int
    user_msg_id: int
    text: str
    resume_token: ResumeToken | None
    context: RunContext | None
    thread_id: int | None
    engine_override: EngineId | None
    timestamp: float


@dataclass(slots=True)
class MessageBatch:
    """A batch of messages ready for dispatch."""

    chat_id: int
    first_msg_id: int
    last_msg_id: int
    combined_text: str
    resume_token: ResumeToken | None
    context: RunContext | None
    thread_id: int | None
    engine_override: EngineId | None


TopicKey = tuple[int, int | None]  # (chat_id, thread_id)


@dataclass
class _PendingBatch:
    """Internal state for a pending batch."""

    messages: list[PendingMessage] = field(default_factory=list)
    deadline: float = 0.0


class TopicDebouncer:
    """Groups messages by topic key within a configurable time window.

    - Groups messages by topic key (chat_id + thread_id)
    - Waits for a configurable window before dispatching
    - Combines multiple messages with newlines as separator
    - Uses first message's resume token, context, and engine override
    - Replies to the last message in the batch
    """

    def __init__(self, window_ms: float = 200.0) -> None:
        self._window_s = window_ms / 1000.0
        self._pending: dict[TopicKey, _PendingBatch] = {}

    @property
    def window_ms(self) -> float:
        """Return the configured window in milliseconds."""
        return self._window_s * 1000.0

    def add_message(self, msg: PendingMessage) -> MessageBatch | None:
        """Add a message to be debounced.

        If debouncing is disabled (window_ms=0), returns a batch immediately.
        Otherwise, adds to pending and returns None.
        """
        if self._window_s <= 0:
            return MessageBatch(
                chat_id=msg.chat_id,
                first_msg_id=msg.user_msg_id,
                last_msg_id=msg.user_msg_id,
                combined_text=msg.text,
                resume_token=msg.resume_token,
                context=msg.context,
                thread_id=msg.thread_id,
                engine_override=msg.engine_override,
            )

        key: TopicKey = (msg.chat_id, msg.thread_id)
        now = msg.timestamp

        batch = self._pending.get(key)
        if batch is None:
            batch = _PendingBatch()
            self._pending[key] = batch

        batch.messages.append(msg)
        batch.deadline = now + self._window_s
        return None

    def check_expired(self, now: float) -> list[MessageBatch]:
        """Check for batches whose deadline has passed.

        Returns all expired batches and removes them from pending.
        """
        expired: list[MessageBatch] = []
        expired_keys: list[TopicKey] = []

        for key, batch in self._pending.items():
            if batch.deadline <= now and batch.messages:
                expired.append(self._finalize_batch(batch))
                expired_keys.append(key)

        for key in expired_keys:
            del self._pending[key]

        return expired

    def next_deadline(self) -> float | None:
        """Return the earliest deadline among pending batches, or None if empty."""
        if not self._pending:
            return None
        return min(batch.deadline for batch in self._pending.values() if batch.messages)

    def flush_all(self) -> list[MessageBatch]:
        """Flush and return all pending batches immediately."""
        batches: list[MessageBatch] = []
        for batch in self._pending.values():
            if batch.messages:
                batches.append(self._finalize_batch(batch))
        self._pending.clear()
        return batches

    def flush_topic(self, key: TopicKey) -> MessageBatch | None:
        """Flush and return the pending batch for a specific topic, if any."""
        batch = self._pending.pop(key, None)
        if batch is None or not batch.messages:
            return None
        return self._finalize_batch(batch)

    def _finalize_batch(self, batch: _PendingBatch) -> MessageBatch:
        """Convert a pending batch to a finalized MessageBatch."""
        messages = batch.messages
        first = messages[0]
        last = messages[-1]
        combined_text = "\n".join(m.text for m in messages)
        return MessageBatch(
            chat_id=first.chat_id,
            first_msg_id=first.user_msg_id,
            last_msg_id=last.user_msg_id,
            combined_text=combined_text,
            resume_token=first.resume_token,
            context=first.context,
            thread_id=first.thread_id,
            engine_override=first.engine_override,
        )
