from datetime import datetime
from typing import Literal, final

@final
class User:
    id: str
    username: str
    global_name: str | None
    bot: bool

@final
class QrUser:
    id: str
    discriminator: str
    avatar: str | None
    username: str

@final
class AuthState:
    version: int
    kind: str
    mfa_methods: list[str]
    qr_url: str | None
    qr_expires_at: datetime | None
    qr_user: QrUser | None
    user: User | None
    failure: str | None
    retry_after: float | None
    session_persisted: bool

@final
class Role:
    id: str
    name: str
    color: int

@final
class Channel:
    id: str
    kind: int
    name: str | None
    guild_id: str | None
    position: int
    parent_id: str | None
    last_message_id: str | None
    topic: str | None
    recipients: list[User]

@final
class Guild:
    id: str
    name: str
    icon: str | None
    channels: list[Channel]
    roles: list[Role]
    unavailable: bool

@final
class Attachment:
    filename: str
    url: str
    size: int

@final
class MessageReference:
    message_id: str
    channel_id: str
    guild_id: str | None
    def __init__(self, message_id: str, channel_id: str, guild_id: str | None = None) -> None: ...

@final
class Message:
    id: str
    channel_id: str
    guild_id: str | None
    author: User
    author_nick: str | None
    content: str
    timestamp: datetime
    edited_timestamp: datetime | None
    kind: int
    mention_ids: list[str]
    mention_role_ids: list[str]
    mention_everyone: bool
    attachments: list[Attachment]
    embed_titles: list[str]
    nonce: str | None
    @property
    def referenced(self) -> Message | None: ...

@final
class ReadState:
    channel_id: str
    last_message_id: str | None
    mention_count: int

@final
class Ready:
    user: User
    guilds: list[Guild]
    private_channels: list[Channel]
    read_states: list[ReadState]
    users: list[User]

@final
class GatewayEvent:
    kind: str
    message: Message | None
    message_id: str | None
    channel_id: str | None
    guild_id: str | None
    user_id: str | None
    timestamp: int | None
    mention_count: int | None
    channel: Channel | None
    guild: Guild | None
    ready: Ready | None
    status: str | None
    retry_in: float | None
    unavailable: bool | None

@final
class Session:
    def __init__(
        self,
        api_base: str | None = None,
        remote_auth_url: str | None = None,
        credential_store: Literal["system", "memory"] = "system",
        locale: str = "en-US",
        gateway_url: str | None = None,
        reconnect_base: float | None = None,
    ) -> None: ...
    def auth_state(self) -> AuthState: ...
    def cancel_qr_login(self) -> None: ...
    def close(self) -> None: ...
    def subscribe_guild(self, guild_id: str) -> None: ...
    def disconnect_gateway(self) -> None: ...
    async def wait_auth_change(self, after_version: int) -> AuthState: ...
    async def resume_session(self) -> AuthState: ...
    async def login_password(self, login: str, password: str) -> AuthState: ...
    async def submit_mfa(self, method: str, code: str) -> AuthState: ...
    async def send_mfa_sms(self) -> str | None: ...
    async def start_qr_login(self) -> AuthState: ...
    async def logout(self) -> AuthState: ...
    async def set_remember_session(self, enabled: bool) -> AuthState: ...
    async def connect_gateway(self) -> Ready: ...
    async def next_event(self) -> GatewayEvent | None: ...
    async def fetch_guilds(self) -> list[Guild]: ...
    async def fetch_guild_channels(self, guild_id: str) -> list[Channel]: ...
    async def fetch_private_channels(self) -> list[Channel]: ...
    async def fetch_messages(
        self, channel_id: str, before: str | None = None, limit: int = 50
    ) -> list[Message]: ...
    async def send_message(
        self,
        channel_id: str,
        content: str,
        reply_to: MessageReference | None = None,
        nonce: str | None = None,
    ) -> Message: ...
