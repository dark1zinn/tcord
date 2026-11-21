"""In-memory ClientProtocol implementation driving the TUI in pilot tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from client.errors import CaptchaRequiredError, NetworkError, NotFoundError
from client.models import (
    AuthFailure,
    AuthKind,
    AuthState,
    Channel,
    ChannelKind,
    GatewayEvent,
    Guild,
    MessageInfo,
    MessageReference,
    MfaMethod,
    QrUser,
    ReadState,
    ReadySnapshot,
    ThreadPage,
    User,
)

ME = User("100000000000000001", "me", "Me")
FRIEND = User("100000000000000002", "friend", "Friend")
OTHER = User("100000000000000003", "other", "Other")
GUILD_ID = "200000000000000001"
GENERAL = "300000000000000001"
RANDOM = "300000000000000002"
VOICE = "300000000000000003"
CATEGORY = "300000000000000004"
DM = "300000000000000010"
BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
MFA_CODE = "123456"


def make_message(
    message_id: int,
    channel_id: str = GENERAL,
    author: User = FRIEND,
    content: str | None = None,
    **extra: Any,
) -> MessageInfo:
    return MessageInfo(
        id=str(400000000000000000 + message_id),
        channel_id=channel_id,
        author=author,
        content=content if content is not None else f"message {message_id}",
        timestamp=BASE_TIME + timedelta(minutes=message_id * 10),
        guild_id=GUILD_ID if channel_id != DM else None,
        **extra,
    )


def snapshot() -> ReadySnapshot:
    guild = Guild(
        GUILD_ID,
        "Tcord Testers",
        channels=(
            Channel(CATEGORY, ChannelKind.CATEGORY, "Text", GUILD_ID, position=0),
            Channel(
                GENERAL,
                ChannelKind.TEXT,
                "general",
                GUILD_ID,
                position=0,
                parent_id=CATEGORY,
                last_message_id=str(400000000000000000 + 30),
            ),
            Channel(
                RANDOM,
                ChannelKind.TEXT,
                "random",
                GUILD_ID,
                position=1,
                parent_id=CATEGORY,
                last_message_id=str(400000000000000000 + 2),
            ),
            Channel(VOICE, ChannelKind.VOICE, "Lounge", GUILD_ID, position=2, parent_id=CATEGORY),
        ),
    )
    dm = Channel(
        DM, ChannelKind.DM, recipients=(FRIEND,), last_message_id=str(400000000000000000 + 1)
    )
    return ReadySnapshot(
        user=ME,
        guilds=(guild,),
        private_channels=(dm,),
        read_states=(
            ReadState(GENERAL, str(400000000000000000 + 30), 0),
            ReadState(RANDOM, str(400000000000000000 + 2), 0),
            ReadState(DM, str(400000000000000000 + 1), 0),
        ),
        users=(FRIEND, OTHER),
    )


class FakeAuth:
    def __init__(self, *, stored_session: bool = True, mfa: bool = False) -> None:
        self.stored_session = stored_session
        self.mfa = mfa
        self.calls: list[str] = []
        self.captcha = False
        self.restore_network_failure = False
        self._state = AuthState(version=0, kind=AuthKind.LOGGED_OUT)
        self._changed = asyncio.Event()
        self.on_logout: list[Callable[[], None]] = []

    def set(self, kind: AuthKind, **fields: Any) -> AuthState:
        self._state = AuthState(version=self._state.version + 1, kind=kind, **fields)
        self._changed.set()
        self._changed = asyncio.Event()
        return self._state

    def state(self) -> AuthState:
        return self._state

    async def wait_for_change(self, after_version: int) -> AuthState:
        while self._state.version <= after_version:
            await self._changed.wait()
        return self._state

    async def resume_session(self) -> AuthState:
        self.calls.append("resume")
        self.set(AuthKind.RESTORING)
        await asyncio.sleep(0)
        if self.restore_network_failure:
            return self.set(AuthKind.LOGGED_OUT, failure=AuthFailure.NETWORK)
        if self.stored_session:
            return self.set(AuthKind.AUTHENTICATED, user=ME, session_persisted=True)
        return self.set(AuthKind.LOGGED_OUT)

    async def login_password(self, login: str, password: str) -> AuthState:
        self.calls.append(f"login:{login}")
        if self.captcha:
            raise CaptchaRequiredError("captcha", "hcaptcha", "key")
        if self.mfa:
            return self.set(AuthKind.WAITING_FOR_MFA, mfa_methods=(MfaMethod.TOTP, MfaMethod.SMS))
        return self.set(AuthKind.AUTHENTICATED, user=ME, session_persisted=True)

    async def submit_mfa(self, method: MfaMethod, code: str) -> AuthState:
        self.calls.append(f"mfa:{method.value}:{code}")
        if code == MFA_CODE:
            return self.set(AuthKind.AUTHENTICATED, user=ME, session_persisted=True)
        return self.set(
            AuthKind.WAITING_FOR_MFA,
            mfa_methods=(MfaMethod.TOTP, MfaMethod.SMS),
            failure=AuthFailure.INVALID_CODE,
        )

    async def send_sms_code(self) -> str | None:
        self.calls.append("sms")
        return "******1234"

    async def start_qr_login(self) -> AuthState:
        self.calls.append("qr:start")
        return self.set(AuthKind.WAITING_FOR_QR)

    def qr_ready(self) -> None:
        self.set(
            AuthKind.WAITING_FOR_QR,
            qr_url="https://discord.com/ra/abcdef",
            qr_expires_at=datetime.now(UTC) + timedelta(minutes=2),
        )

    def qr_scanned(self) -> None:
        self.set(
            AuthKind.WAITING_FOR_QR,
            qr_url="https://discord.com/ra/abcdef",
            qr_user=QrUser("1", "0", None, "scanner"),
        )

    def cancel_qr_login(self) -> None:
        self.calls.append("qr:cancel")
        self.set(AuthKind.LOGGED_OUT, failure=AuthFailure.QR_CANCELLED)

    async def logout(self) -> AuthState:
        self.calls.append("logout")
        for callback in self.on_logout:
            callback()
        return self.set(AuthKind.LOGGED_OUT)

    async def set_remember_session(self, enabled: bool) -> AuthState:
        self.calls.append(f"remember:{enabled}")
        return self._state


class FakeGateway:
    def __init__(self) -> None:
        self.events: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self.subscribed: list[str] = []
        self.member_requests: list[tuple[str, tuple[str, ...]]] = []
        self.thread_calls: list[tuple[str, int]] = []
        self.thread_pages: dict[str, list[Channel]] = {}
        self.thread_page_size: int | None = None
        self.thread_failures = 0
        self.connects = 0
        self.snapshot = snapshot()

    async def connect(self) -> ReadySnapshot:
        self.connects += 1
        self.events = asyncio.Queue()
        return self.snapshot

    async def next_event(self) -> GatewayEvent | None:
        return await self.events.get()

    def push(self, event: GatewayEvent) -> None:
        self.events.put_nowait(event)

    def stop(self) -> None:
        self.events.put_nowait(None)

    def subscribe_guild(self, guild_id: str) -> None:
        self.subscribed.append(guild_id)

    def disconnect(self) -> None:
        self.stop()

    async def guilds(self) -> list[Guild]:
        return list(self.snapshot.guilds)

    async def guild_channels(self, guild_id: str) -> list[Channel]:
        return [c for g in self.snapshot.guilds if g.id == guild_id for c in g.channels]

    async def private_channels(self) -> list[Channel]:
        return list(self.snapshot.private_channels)

    def request_members(self, guild_id: str, user_ids: list[str]) -> None:
        self.member_requests.append((guild_id, tuple(user_ids)))

    async def threads(
        self, channel_id: str, *, archived: bool = False, offset: int = 0
    ) -> ThreadPage:
        self.thread_calls.append((channel_id, offset))
        if self.thread_failures:
            self.thread_failures -= 1
            raise NetworkError("search unavailable")
        posts = self.thread_pages.get(channel_id, [])
        size = self.thread_page_size or len(posts)
        page = posts[offset : offset + size]
        return ThreadPage(tuple(page), has_more=offset + size < len(posts))


class FakeMedia:
    """Serves registered URLs; anything else is missing (like an expired CDN link)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.requests: list[str] = []

    async def fetch(self, url: str) -> bytes:
        self.requests.append(url)
        if url not in self.files:
            raise NotFoundError("missing media")
        return self.files[url]


class FakeMessages:
    def __init__(self) -> None:
        self.histories: dict[str, list[MessageInfo]] = {
            GENERAL: [make_message(i, author=ME if i % 5 == 0 else FRIEND) for i in range(1, 31)],
            RANDOM: [make_message(1, RANDOM), make_message(2, RANDOM)],
            DM: [make_message(1, DM)],
        }
        self.history_calls: list[tuple[str, str | None]] = []
        self.sent: list[tuple[str, str, MessageReference | None, str | None]] = []
        self.edits: list[tuple[str, str, str]] = []
        self.deletes: list[tuple[str, str]] = []
        self.typing: list[str] = []
        self.acks: list[tuple[str, str]] = []
        self.fail_sends = 0
        self._next_id = 1000

    async def history(
        self, channel_id: str, before: str | None = None, limit: int = 50
    ) -> list[MessageInfo]:
        self.history_calls.append((channel_id, before))
        messages = self.histories.get(channel_id, [])
        if before is not None:
            messages = [m for m in messages if int(m.id) < int(before)]
        return messages[-limit:]

    async def send(
        self,
        channel_id: str,
        content: str,
        *,
        reply_to: MessageReference | None = None,
        nonce: str | None = None,
    ) -> MessageInfo:
        self.sent.append((channel_id, content, reply_to, nonce))
        if self.fail_sends:
            self.fail_sends -= 1
            raise NetworkError("offline")
        self._next_id += 1
        return replace(make_message(self._next_id, channel_id, ME, content), nonce=nonce)

    async def edit(self, channel_id: str, message_id: str, content: str) -> MessageInfo:
        self.edits.append((channel_id, message_id, content))
        original = next(m for m in self.histories.get(channel_id, []) if m.id == message_id)
        return replace(original, content=content, edited_timestamp=BASE_TIME)

    async def delete(self, channel_id: str, message_id: str) -> None:
        self.deletes.append((channel_id, message_id))

    async def trigger_typing(self, channel_id: str) -> None:
        self.typing.append(channel_id)

    async def ack(self, channel_id: str, message_id: str) -> None:
        self.acks.append((channel_id, message_id))


class FakeClient:
    def __init__(self, *, stored_session: bool = True, mfa: bool = False) -> None:
        self.auth = FakeAuth(stored_session=stored_session, mfa=mfa)
        self.gateway = FakeGateway()
        self.messages = FakeMessages()
        self.media = FakeMedia()
        self.auth.on_logout.append(self.gateway.stop)
        self.closed = False

    def close(self) -> None:
        self.closed = True
