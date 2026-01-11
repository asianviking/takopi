"""Tests for the message debouncer."""

import pytest

from takopi.context import RunContext
from takopi.model import ResumeToken
from takopi.telegram.debouncer import (
    MessageBatch,
    PendingMessage,
    TopicDebouncer,
)


def _make_pending(
    chat_id: int = 1,
    user_msg_id: int = 100,
    text: str = "hello",
    thread_id: int | None = None,
    timestamp: float = 0.0,
    resume_token: ResumeToken | None = None,
    context: RunContext | None = None,
    engine_override: str | None = None,
) -> PendingMessage:
    return PendingMessage(
        chat_id=chat_id,
        user_msg_id=user_msg_id,
        text=text,
        resume_token=resume_token,
        context=context,
        thread_id=thread_id,
        engine_override=engine_override,
        timestamp=timestamp,
    )


class TestTopicDebouncer:
    """Tests for TopicDebouncer class."""

    def test_disabled_returns_batch_immediately(self) -> None:
        """When window_ms=0, messages are dispatched immediately."""
        debouncer = TopicDebouncer(window_ms=0)
        msg = _make_pending(text="hello")
        batch = debouncer.add_message(msg)

        assert batch is not None
        assert batch.combined_text == "hello"
        assert batch.chat_id == 1
        assert batch.first_msg_id == 100
        assert batch.last_msg_id == 100

    def test_single_message_dispatched_after_window_expires(self) -> None:
        """A single message should dispatch after the window expires."""
        debouncer = TopicDebouncer(window_ms=200)
        msg = _make_pending(text="hello", timestamp=0.0)
        batch = debouncer.add_message(msg)

        assert batch is None
        assert debouncer.next_deadline() == pytest.approx(0.2)

        # Before deadline: no expired batches
        expired = debouncer.check_expired(0.1)
        assert expired == []

        # At/after deadline: batch is dispatched
        expired = debouncer.check_expired(0.2)
        assert len(expired) == 1
        assert expired[0].combined_text == "hello"

    def test_multiple_rapid_messages_batched_together(self) -> None:
        """Multiple messages within window should be batched."""
        debouncer = TopicDebouncer(window_ms=200)

        msg1 = _make_pending(user_msg_id=100, text="line1", timestamp=0.0)
        msg2 = _make_pending(user_msg_id=101, text="line2", timestamp=0.05)
        msg3 = _make_pending(user_msg_id=102, text="line3", timestamp=0.1)

        assert debouncer.add_message(msg1) is None
        assert debouncer.add_message(msg2) is None
        assert debouncer.add_message(msg3) is None

        # Deadline resets with each message, so it's now 0.1 + 0.2 = 0.3
        assert debouncer.next_deadline() == pytest.approx(0.3)

        # After final deadline
        expired = debouncer.check_expired(0.35)
        assert len(expired) == 1
        batch = expired[0]
        assert batch.combined_text == "line1\nline2\nline3"
        assert batch.first_msg_id == 100
        assert batch.last_msg_id == 102

    def test_per_topic_isolation(self) -> None:
        """Different threads don't mix batches."""
        debouncer = TopicDebouncer(window_ms=200)

        # Messages to thread 10
        msg1 = _make_pending(
            chat_id=1, user_msg_id=100, text="thread10-msg1", thread_id=10, timestamp=0.0
        )
        msg2 = _make_pending(
            chat_id=1, user_msg_id=101, text="thread10-msg2", thread_id=10, timestamp=0.05
        )

        # Messages to thread 20
        msg3 = _make_pending(
            chat_id=1, user_msg_id=200, text="thread20-msg1", thread_id=20, timestamp=0.02
        )

        # Different chat
        msg4 = _make_pending(
            chat_id=2, user_msg_id=300, text="chat2-msg1", thread_id=10, timestamp=0.03
        )

        debouncer.add_message(msg1)
        debouncer.add_message(msg2)
        debouncer.add_message(msg3)
        debouncer.add_message(msg4)

        # After all deadlines expire
        expired = debouncer.check_expired(1.0)
        assert len(expired) == 3

        # Sort by first_msg_id for deterministic assertions
        expired.sort(key=lambda b: b.first_msg_id)

        # Thread 10 in chat 1
        assert expired[0].combined_text == "thread10-msg1\nthread10-msg2"
        assert expired[0].thread_id == 10
        assert expired[0].chat_id == 1

        # Thread 20 in chat 1
        assert expired[1].combined_text == "thread20-msg1"
        assert expired[1].thread_id == 20
        assert expired[1].chat_id == 1

        # Thread 10 in chat 2
        assert expired[2].combined_text == "chat2-msg1"
        assert expired[2].chat_id == 2

    def test_resume_token_from_first_message_preserved(self) -> None:
        """Resume token from first message is used in the batch."""
        debouncer = TopicDebouncer(window_ms=200)
        token = ResumeToken(engine="codex", value="resume123")

        msg1 = _make_pending(
            user_msg_id=100, text="first", timestamp=0.0, resume_token=token
        )
        msg2 = _make_pending(
            user_msg_id=101,
            text="second",
            timestamp=0.05,
            resume_token=ResumeToken(engine="other", value="ignored"),
        )

        debouncer.add_message(msg1)
        debouncer.add_message(msg2)

        expired = debouncer.check_expired(1.0)
        assert len(expired) == 1
        assert expired[0].resume_token == token

    def test_context_from_first_message_preserved(self) -> None:
        """Context from first message is used in the batch."""
        debouncer = TopicDebouncer(window_ms=200)
        context = RunContext(project="myproj", branch="feat/test")

        msg1 = _make_pending(user_msg_id=100, text="first", timestamp=0.0, context=context)
        msg2 = _make_pending(
            user_msg_id=101,
            text="second",
            timestamp=0.05,
            context=RunContext(project="other", branch="main"),
        )

        debouncer.add_message(msg1)
        debouncer.add_message(msg2)

        expired = debouncer.check_expired(1.0)
        assert len(expired) == 1
        assert expired[0].context == context

    def test_engine_override_from_first_message_preserved(self) -> None:
        """Engine override from first message is used in the batch."""
        debouncer = TopicDebouncer(window_ms=200)

        msg1 = _make_pending(
            user_msg_id=100, text="first", timestamp=0.0, engine_override="claude"
        )
        msg2 = _make_pending(
            user_msg_id=101, text="second", timestamp=0.05, engine_override="codex"
        )

        debouncer.add_message(msg1)
        debouncer.add_message(msg2)

        expired = debouncer.check_expired(1.0)
        assert len(expired) == 1
        assert expired[0].engine_override == "claude"

    def test_flush_all_returns_all_pending(self) -> None:
        """flush_all() returns all pending batches immediately."""
        debouncer = TopicDebouncer(window_ms=200)

        debouncer.add_message(_make_pending(user_msg_id=100, text="msg1", thread_id=10))
        debouncer.add_message(_make_pending(user_msg_id=101, text="msg2", thread_id=20))

        batches = debouncer.flush_all()
        assert len(batches) == 2

        # After flush, no pending batches
        assert debouncer.next_deadline() is None
        assert debouncer.check_expired(1.0) == []

    def test_flush_topic_returns_specific_batch(self) -> None:
        """flush_topic() returns only the specified topic's batch."""
        debouncer = TopicDebouncer(window_ms=200)

        debouncer.add_message(
            _make_pending(chat_id=1, user_msg_id=100, text="t10", thread_id=10)
        )
        debouncer.add_message(
            _make_pending(chat_id=1, user_msg_id=101, text="t20", thread_id=20)
        )

        batch = debouncer.flush_topic((1, 10))
        assert batch is not None
        assert batch.combined_text == "t10"

        # Thread 20 should still be pending
        assert debouncer.next_deadline() is not None
        remaining = debouncer.flush_all()
        assert len(remaining) == 1
        assert remaining[0].combined_text == "t20"

    def test_flush_topic_returns_none_for_empty(self) -> None:
        """flush_topic() returns None if no pending messages for that topic."""
        debouncer = TopicDebouncer(window_ms=200)
        assert debouncer.flush_topic((1, 10)) is None

    def test_deadline_resets_when_new_message_arrives(self) -> None:
        """Deadline should reset when a new message arrives for the same topic."""
        debouncer = TopicDebouncer(window_ms=200)

        msg1 = _make_pending(user_msg_id=100, text="msg1", timestamp=0.0)
        debouncer.add_message(msg1)
        assert debouncer.next_deadline() == pytest.approx(0.2)

        msg2 = _make_pending(user_msg_id=101, text="msg2", timestamp=0.15)
        debouncer.add_message(msg2)
        assert debouncer.next_deadline() == pytest.approx(0.35)

        # Check at original deadline: should not expire yet
        expired = debouncer.check_expired(0.25)
        assert expired == []

        # Check at new deadline
        expired = debouncer.check_expired(0.4)
        assert len(expired) == 1
        assert expired[0].combined_text == "msg1\nmsg2"

    def test_window_ms_property(self) -> None:
        """window_ms property returns configured value."""
        debouncer = TopicDebouncer(window_ms=500)
        assert debouncer.window_ms == 500

    def test_no_thread_id_uses_none_key(self) -> None:
        """Messages without thread_id are grouped under None."""
        debouncer = TopicDebouncer(window_ms=200)

        msg1 = _make_pending(user_msg_id=100, text="no-thread-1", thread_id=None)
        msg2 = _make_pending(user_msg_id=101, text="no-thread-2", thread_id=None)

        debouncer.add_message(msg1)
        debouncer.add_message(msg2)

        expired = debouncer.check_expired(1.0)
        assert len(expired) == 1
        assert expired[0].combined_text == "no-thread-1\nno-thread-2"
        assert expired[0].thread_id is None
