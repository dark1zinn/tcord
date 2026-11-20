from dataclasses import replace
from datetime import UTC, datetime

from client.models import (
    Channel,
    ChannelKind,
    Guild,
    MessageAcked,
    MessageCreated,
    MessageDeleted,
    MessageInfo,
    MessageUpdated,
    ReadState,
    ReadySnapshot,
    TypingStarted,
    User,
)
from client.state import ChatStore, MessagesChanged, TypingChanged

ME = User("1", "me")
FRIEND = User("2", "friend")
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def message(
    message_id: str,
    channel_id: str = "10",
    author: User = FRIEND,
    *,
    content: str = "hi",
    nonce: str | None = None,
    mentions: tuple[str, ...] = (),
    everyone: bool = False,
) -> MessageInfo:
    return MessageInfo(
        id=message_id,
        channel_id=channel_id,
        author=author,
        content=content,
        timestamp=NOW,
        nonce=nonce,
        mention_ids=mentions,
        mention_everyone=everyone,
    )


def store(**kwargs: int) -> ChatStore:
    chat = ChatStore(clock=lambda: 0.0, **kwargs)
    guild = Guild(
        "100",
        "Guild",
        channels=(
            Channel("10", ChannelKind.TEXT, "general", "100", last_message_id="5"),
            Channel("11", ChannelKind.TEXT, "random", "100", last_message_id="3"),
        ),
    )
    dm = Channel("20", ChannelKind.DM, last_message_id="7", recipients=(FRIEND,))
    chat.load_ready(
        ReadySnapshot(
            user=ME,
            guilds=(guild,),
            private_channels=(dm,),
            read_states=(ReadState("10", "5", 0), ReadState("11", "1", 0), ReadState("20", "7", 0)),
            users=(FRIEND,),
        )
    )
    return chat


def test_ready_computes_unread_from_read_states() -> None:
    chat = store()
    assert not chat.is_unread("10")
    assert chat.is_unread("11")
    assert chat.guild_badge("100") == (True, 0)
    assert chat.dm_badge() == (False, 0)


def test_incoming_message_marks_unread_and_counts_mentions() -> None:
    chat = store()
    chat.apply(MessageCreated(message("6")))
    assert chat.is_unread("10")
    assert chat.mention_count("10") == 0

    chat.apply(MessageCreated(message("8", mentions=("1",))))
    chat.apply(MessageCreated(message("9", everyone=True)))
    assert chat.mention_count("10") == 2


def test_every_dm_message_counts_as_mention() -> None:
    chat = store()
    chat.apply(MessageCreated(message("8", channel_id="20")))
    assert chat.dm_badge() == (True, 1)


def test_own_message_clears_unread() -> None:
    chat = store()
    chat.apply(MessageCreated(message("8", mentions=("1",))))
    chat.apply(MessageCreated(message("9", author=ME)))
    assert not chat.is_unread("10")
    assert chat.mention_count("10") == 0


def test_ack_and_mark_read_clear_badges() -> None:
    chat = store()
    chat.apply(MessageCreated(message("8", mentions=("1",))))
    chat.apply(MessageAcked("10", "8", 0))
    assert not chat.is_unread("10")
    chat.apply(MessageCreated(message("9")))
    chat.mark_read("10", "9")
    assert not chat.is_unread("10")


def test_pending_resolves_once_when_rest_answers_first() -> None:
    chat = store()
    chat.set_history("10", [message("5")], older=False)
    chat.add_pending(message("0", author=ME, content="x", nonce="n1"))
    sent = message("12", author=ME, content="x", nonce="n1")

    chat.resolve_pending("n1", sent)
    chat.apply(MessageCreated(sent))

    assert [m.id for m in chat.history("10")] == ["5", "12"]
    assert not any(m.pending for m in chat.history("10"))


def test_pending_resolves_once_when_gateway_answers_first() -> None:
    chat = store()
    chat.set_history("10", [message("5")], older=False)
    chat.add_pending(message("0", author=ME, content="x", nonce="n1"))
    sent = message("12", author=ME, content="x", nonce="n1")

    chat.apply(MessageCreated(sent))
    chat.resolve_pending("n1", sent)

    assert [m.id for m in chat.history("10")] == ["5", "12"]


def test_failed_pending_is_flagged() -> None:
    chat = store()
    chat.set_history("10", [], older=False)
    chat.add_pending(message("0", author=ME, nonce="n1"))
    chat.fail_pending("n1")
    assert chat.history("10")[0].failed


def test_update_and_delete_ops() -> None:
    chat = store()
    chat.set_history("10", [message("5"), message("6")], older=False)

    changes = chat.apply(MessageUpdated(replace(message("6"), content="edited")))
    assert changes == [MessagesChanged("10", "update", "6")]
    assert chat.history("10")[1].content == "edited"

    assert chat.apply(MessageDeleted("5", "10", None)) == [MessagesChanged("10", "remove", "5")]
    assert [m.id for m in chat.history("10")] == ["6"]
    assert chat.apply(MessageUpdated(message("99"))) == []


def test_history_cap_drops_oldest_and_allows_paging() -> None:
    chat = store(history_limit=3)
    chat.set_history("10", [message("1"), message("2")], older=False, limit=2)
    for message_id in ("3", "4"):
        chat.apply(MessageCreated(message(message_id)))
    state = chat.history_state("10")
    assert state is not None
    assert [m.id for m in chat.history("10")] == ["2", "3", "4"]
    assert state.has_more_before


def test_older_page_prepends_without_duplicates() -> None:
    chat = store()
    chat.set_history("10", [message("5"), message("6")], older=False, limit=2)
    chat.set_history("10", [message("4"), message("5")], older=True, limit=50)
    assert [m.id for m in chat.history("10")] == ["4", "5", "6"]
    state = chat.history_state("10")
    assert state is not None
    assert not state.has_more_before


def test_lru_eviction_keeps_active_channel() -> None:
    chat = store(max_cached_channels=2)
    chat.set_active("10")
    chat.set_history("10", [message("5")], older=False)
    chat.set_history("11", [message("2", channel_id="11")], older=False)
    chat.set_history("20", [message("7", channel_id="20")], older=False)
    assert set(chat.histories) == {"10", "20"}


def test_typing_expires_and_is_cleared_by_message() -> None:
    chat = store()
    assert chat.apply(TypingStarted("10", "100", "2", 0)) == [TypingChanged("10")]
    assert chat.apply(TypingStarted("10", "100", "1", 0)) == []  # own typing ignored
    assert [user.username for user in chat.typing_users("10")] == ["friend"]
    assert chat.typing_users("10", now=11.0) == []

    chat.apply(TypingStarted("10", "100", "2", 0))
    changes = chat.apply(MessageCreated(message("8")))
    assert TypingChanged("10") in changes
    assert chat.typing_users("10") == []
