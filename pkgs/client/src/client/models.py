"""Client data model: Discord objects, auth state and gateway events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum


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


class AuthKind(StrEnum):
    LOGGED_OUT = "logged_out"
    RESTORING = "restoring"
    WAITING_FOR_CREDENTIALS = "waiting_for_credentials"
    WAITING_FOR_MFA = "waiting_for_mfa"
    WAITING_FOR_QR = "waiting_for_qr"
    AUTHENTICATED = "authenticated"
    FAILED = "failed"


class MfaMethod(StrEnum):
    TOTP = "totp"
    SMS = "sms"
    BACKUP = "backup"


class AuthFailure(StrEnum):
    INVALID_CREDENTIALS = "invalid_credentials"
    INVALID_CODE = "invalid_code"
    CAPTCHA_REQUIRED = "captcha_required"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    QR_CANCELLED = "qr_cancelled"
    QR_EXPIRED = "qr_expired"
    PHONE_VERIFICATION_REQUIRED = "phone_verification_required"
    ACCOUNT_DISABLED = "account_disabled"
    WEBAUTHN_UNSUPPORTED = "webauthn_unsupported"
    SESSION_EXPIRED = "session_expired"
    CREDENTIAL_STORE = "credential_store"


@dataclass(frozen=True, slots=True)
class QrUser:
    id: str
    discriminator: str
    avatar: str | None
    username: str


@dataclass(frozen=True, slots=True)
class AuthState:
    version: int
    kind: AuthKind
    mfa_methods: tuple[MfaMethod, ...] = ()
    qr_url: str | None = None
    qr_expires_at: datetime | None = None
    qr_user: QrUser | None = None
    user: User | None = None
    failure: AuthFailure | None = None
    retry_after: float | None = None
    session_persisted: bool = False


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
