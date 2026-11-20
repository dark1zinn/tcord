"""Discord text-chat objects as immutable, presentation-safe dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum


@dataclass(frozen=True, slots=True)
class User:
    id: str
    username: str
    global_name: str | None = None
    bot: bool = False

    @property
    def display_name(self) -> str:
        return self.global_name or self.username


@dataclass(frozen=True, slots=True)
class Role:
    id: str
    name: str
    color: int


class ChannelKind(IntEnum):
    TEXT = 0
    DM = 1
    VOICE = 2
    GROUP_DM = 3
    CATEGORY = 4
    ANNOUNCEMENT = 5
    ANNOUNCEMENT_THREAD = 10
    PUBLIC_THREAD = 11
    PRIVATE_THREAD = 12
    STAGE = 13
    FORUM = 15
    MEDIA = 16


_READABLE = {ChannelKind.TEXT, ChannelKind.ANNOUNCEMENT, ChannelKind.DM, ChannelKind.GROUP_DM}


@dataclass(frozen=True, slots=True)
class Channel:
    id: str
    kind: int
    name: str | None = None
    guild_id: str | None = None
    position: int = 0
    parent_id: str | None = None
    last_message_id: str | None = None
    topic: str | None = None
    recipients: tuple[User, ...] = ()

    @property
    def is_readable(self) -> bool:
        return self.kind in _READABLE

    @property
    def is_category(self) -> bool:
        return self.kind == ChannelKind.CATEGORY

    @property
    def is_voice(self) -> bool:
        return self.kind in {ChannelKind.VOICE, ChannelKind.STAGE}

    @property
    def is_private(self) -> bool:
        return self.kind in {ChannelKind.DM, ChannelKind.GROUP_DM}


@dataclass(frozen=True, slots=True)
class Guild:
    id: str
    name: str
    icon: str | None = None
    channels: tuple[Channel, ...] = ()
    roles: tuple[Role, ...] = ()
    unavailable: bool = False


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str
    url: str
    size: int


@dataclass(frozen=True, slots=True)
class MessageReference:
    message_id: str
    channel_id: str
    guild_id: str | None = None


@dataclass(frozen=True, slots=True)
class MessageInfo:
    id: str
    channel_id: str
    author: User
    content: str
    timestamp: datetime
    guild_id: str | None = None
    author_nick: str | None = None
    edited_timestamp: datetime | None = None
    kind: int = 0
    mention_ids: tuple[str, ...] = ()
    mention_role_ids: tuple[str, ...] = ()
    mention_everyone: bool = False
    attachments: tuple[Attachment, ...] = ()
    embed_titles: tuple[str, ...] = ()
    referenced: MessageInfo | None = None
    nonce: str | None = None
    pending: bool = False
    failed: bool = False

    @property
    def author_name(self) -> str:
        return self.author_nick or self.author.display_name
