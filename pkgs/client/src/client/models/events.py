"""Gateway snapshot and event dataclasses."""

from dataclasses import dataclass
from enum import StrEnum

from .domain import Channel, Guild, MessageInfo, User


class ConnectionStatus(StrEnum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RESUMING = "resuming"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True, slots=True)
class ReadState:
    channel_id: str
    last_message_id: str | None
    mention_count: int


@dataclass(frozen=True, slots=True)
class ReadySnapshot:
    user: User
    guilds: tuple[Guild, ...]
    private_channels: tuple[Channel, ...]
    read_states: tuple[ReadState, ...]
    users: tuple[User, ...]


@dataclass(frozen=True, slots=True)
class Ready:
    snapshot: ReadySnapshot


@dataclass(frozen=True, slots=True)
class MessageCreated:
    message: MessageInfo


@dataclass(frozen=True, slots=True)
class MessageUpdated:
    message: MessageInfo


@dataclass(frozen=True, slots=True)
class MessageDeleted:
    message_id: str
    channel_id: str
    guild_id: str | None


@dataclass(frozen=True, slots=True)
class TypingStarted:
    channel_id: str
    guild_id: str | None
    user_id: str
    timestamp: int


@dataclass(frozen=True, slots=True)
class MessageAcked:
    channel_id: str
    message_id: str
    mention_count: int | None


@dataclass(frozen=True, slots=True)
class ChannelUpserted:
    channel: Channel


@dataclass(frozen=True, slots=True)
class ChannelDeleted:
    channel_id: str
    guild_id: str | None


@dataclass(frozen=True, slots=True)
class GuildUpserted:
    guild: Guild


@dataclass(frozen=True, slots=True)
class GuildRemoved:
    guild_id: str
    unavailable: bool


@dataclass(frozen=True, slots=True)
class ConnectionChanged:
    status: ConnectionStatus
    retry_in: float | None


@dataclass(frozen=True, slots=True)
class SessionInvalidated:
    pass


type GatewayEvent = (
    Ready
    | MessageCreated
    | MessageUpdated
    | MessageDeleted
    | TypingStarted
    | MessageAcked
    | ChannelUpserted
    | ChannelDeleted
    | GuildUpserted
    | GuildRemoved
    | ConnectionChanged
    | SessionInvalidated
)
