"""In-memory chat state built from READY plus gateway events.

The store is UI-agnostic: every mutation returns the list of :class:`StoreChange` values the
presentation layer needs to refresh.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Literal

from .models import (
    Channel,
    ChannelDeleted,
    ChannelUpserted,
    ConnectionChanged,
    ConnectionStatus,
    GatewayEvent,
    Guild,
    GuildRemoved,
    GuildUpserted,
    Member,
    MembersLoaded,
    MessageAcked,
    MessageCreated,
    MessageDeleted,
    MessageInfo,
    MessageUpdated,
    ReadState,
    Ready,
    ReadySnapshot,
    Role,
    RoleDeleted,
    RoleUpserted,
    SessionInvalidated,
    ThreadDeleted,
    ThreadListSynced,
    ThreadMembershipChanged,
    ThreadUpserted,
    TypingStarted,
    User,
)
from .permissions import VIEW_CHANNEL, channel_permissions

TYPING_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class GuildsChanged:
    pass


@dataclass(frozen=True, slots=True)
class ChannelsChanged:
    guild_id: str | None


@dataclass(frozen=True, slots=True)
class MessagesChanged:
    channel_id: str
    op: Literal["append", "update", "remove", "reset"]
    message_id: str | None = None


@dataclass(frozen=True, slots=True)
class UnreadChanged:
    channel_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TypingChanged:
    channel_id: str


@dataclass(frozen=True, slots=True)
class ConnectionStatusChanged:
    status: ConnectionStatus
    retry_in: float | None


@dataclass(frozen=True, slots=True)
class SessionEnded:
    pass


@dataclass(frozen=True, slots=True)
class MembersChanged:
    """Nicknames or role colours of ``guild_id`` members changed."""

    guild_id: str


type StoreChange = (
    GuildsChanged
    | ChannelsChanged
    | MessagesChanged
    | UnreadChanged
    | TypingChanged
    | ConnectionStatusChanged
    | SessionEnded
    | MembersChanged
)


@dataclass
class ChannelHistory:
    messages: deque[MessageInfo] = field(default_factory=deque)
    has_more_before: bool = True
    loaded: bool = False

    def index(self, message_id: str) -> int | None:
        for position, message in enumerate(self.messages):
            if message.id == message_id:
                return position
        return None


def _id(value: str | None) -> int:
    return int(value) if value and value.isdigit() else 0


class ChatStore:
    def __init__(
        self,
        history_limit: int = 500,
        max_cached_channels: int = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.history_limit = history_limit
        self.max_cached_channels = max_cached_channels
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self.me: User | None = None
        self.guilds: dict[str, Guild] = {}
        self.channels: dict[str, Channel] = {}
        self.private_channel_ids: list[str] = []
        self.users: dict[str, User] = {}
        self.read_states: dict[str, ReadState] = {}
        self.histories: OrderedDict[str, ChannelHistory] = OrderedDict()
        self.typing: dict[str, dict[str, float]] = {}
        self.pending: dict[str, MessageInfo] = {}
        self.members: dict[str, dict[str, Member]] = {}
        self.active_channel: str | None = None
        self.connection = ConnectionStatus.CONNECTING
        self._requested_members: set[tuple[str, str]] = set()
        self._permissions: dict[str, int | None] = {}

    # ----- loading ---------------------------------------------------------------------------

    def load_ready(self, snapshot: ReadySnapshot) -> list[StoreChange]:
        active = self.active_channel
        self.reset()
        self.me = snapshot.user
        self.users = {user.id: user for user in snapshot.users}
        self.users[snapshot.user.id] = snapshot.user
        for guild in snapshot.guilds:
            self._put_guild(guild)
        for channel in snapshot.private_channels:
            self._put_channel(channel)
        self.read_states = {state.channel_id: state for state in snapshot.read_states}
        self.active_channel = active if active in self.channels else None
        self.connection = ConnectionStatus.CONNECTED
        return [GuildsChanged(), ChannelsChanged(None)]

    def _put_guild(self, guild: Guild) -> None:
        previous = self.guilds.get(guild.id)
        if previous is not None:
            # GUILD_UPDATE and cached GUILD_CREATE payloads omit unchanged collections.
            guild = replace(
                guild,
                channels=guild.channels or previous.channels,
                roles=guild.roles or previous.roles,
                threads=guild.threads or previous.threads,
                me=guild.me or previous.me,
            )
        if guild.channels or previous is None:
            for channel in self.guild_channels(guild.id, include_threads=True):
                self.channels.pop(channel.id, None)
            for channel in (*guild.channels, *guild.threads):
                self._put_channel(replace(channel, guild_id=channel.guild_id or guild.id))
        self.guilds[guild.id] = guild
        if guild.me is not None:
            self._remember_member(guild.id, guild.me)
        self._permissions.clear()

    def _put_channel(self, channel: Channel) -> None:
        self.channels[channel.id] = channel
        for user in channel.recipients:
            self.users.setdefault(user.id, user)
        if channel.is_private and channel.id not in self.private_channel_ids:
            self.private_channel_ids.append(channel.id)
        # Threads inherit their parent's permissions, so drop every cached decision.
        self._permissions.clear()

    def _remember_member(self, guild_id: str, member: Member) -> bool:
        """Store ``member``; ``True`` when it changed the signed-in account's membership."""
        self.members.setdefault(guild_id, {})[member.user_id] = member
        if self.me is not None and member.user_id == self.me.id:
            guild = self.guilds.get(guild_id)
            if guild is not None and guild.me != member:
                self.guilds[guild_id] = replace(guild, me=member)
                self._permissions.clear()
                return True
        return False

    # ----- queries ---------------------------------------------------------------------------

    def guild_channels(self, guild_id: str, *, include_threads: bool = False) -> list[Channel]:
        return [
            channel
            for channel in self.channels.values()
            if channel.guild_id == guild_id and (include_threads or not channel.is_thread)
        ]

    def thread_children(self, parent_id: str) -> list[Channel]:
        """Known threads under ``parent_id``: active ones and archived ones the user joined."""
        threads = [
            channel
            for channel in self.channels.values()
            if channel.is_thread
            and channel.parent_id == parent_id
            and (not channel.is_archived or channel.joined)
        ]
        return sorted(
            threads, key=lambda thread: _id(thread.last_message_id or thread.id), reverse=True
        )

    def set_threads(self, threads: Iterable[Channel]) -> list[StoreChange]:
        """Add threads fetched over REST (forum posts, channel thread lists)."""
        guilds: set[str | None] = set()
        for thread in threads:
            self._put_channel(thread)
            guilds.add(thread.guild_id)
        return [ChannelsChanged(guild_id) for guild_id in guilds]

    def private_channels(self) -> list[Channel]:
        channels = [self.channels[cid] for cid in self.private_channel_ids if cid in self.channels]
        return sorted(channels, key=lambda channel: _id(channel.last_message_id), reverse=True)

    def history(self, channel_id: str) -> list[MessageInfo]:
        history = self.histories.get(channel_id)
        return list(history.messages) if history else []

    def history_state(self, channel_id: str) -> ChannelHistory | None:
        return self.histories.get(channel_id)

    def member(self, guild_id: str | None, user_id: str) -> Member | None:
        if guild_id is None:
            return None
        return self.members.get(guild_id, {}).get(user_id)

    def role_color(self, guild_id: str | None, user_id: str) -> int | None:
        """Colour of the member's highest coloured role, like Discord's name colour."""
        member = self.member(guild_id, user_id)
        guild = self.guilds.get(guild_id or "")
        if member is None or guild is None or not member.roles:
            return None
        roles = {role.id: role for role in guild.roles}
        coloured = [
            roles[role_id] for role_id in member.roles if role_id in roles and roles[role_id].color
        ]
        if not coloured:
            return None
        return max(coloured, key=lambda role: (role.position, _id(role.id))).color

    def missing_members(self, guild_id: str, user_ids: Iterable[str]) -> list[str]:
        """User ids with no cached member that were not requested yet (marks them requested)."""
        known = self.members.get(guild_id, {})
        missing = []
        for user_id in dict.fromkeys(user_ids):
            key = (guild_id, user_id)
            if user_id not in known and key not in self._requested_members:
                self._requested_members.add(key)
                missing.append(user_id)
        return missing

    def permissions(self, channel_id: str) -> int | None:
        """The signed-in user's permissions in a guild channel; ``None`` when unknown."""
        if channel_id in self._permissions:
            return self._permissions[channel_id]
        result = self._compute_permissions(channel_id)
        self._permissions[channel_id] = result
        return result

    def _compute_permissions(self, channel_id: str) -> int | None:
        channel = self.channels.get(channel_id)
        if channel is None or channel.guild_id is None or self.me is None:
            return None
        if channel.is_thread and channel.parent_id is not None:
            return self.permissions(channel.parent_id)
        guild = self.guilds.get(channel.guild_id)
        if guild is None or guild.me is None:
            return None
        return channel_permissions(guild, channel, self.me.id, guild.me.roles)

    def can_view(self, channel_id: str) -> bool:
        """Whether the user may see the channel. Unknown permissions count as visible."""
        permissions = self.permissions(channel_id)
        return permissions is None or bool(permissions & VIEW_CHANNEL)

    def _remember_author(self, message: MessageInfo) -> None:
        self.users.setdefault(message.author.id, message.author)
        if message.member is not None and message.guild_id is not None:
            self.members.setdefault(message.guild_id, {})[message.author.id] = message.member

    def is_unread(self, channel_id: str) -> bool:
        channel = self.channels.get(channel_id)
        if channel is None or not channel.is_readable:
            return False
        state = self.read_states.get(channel_id)
        read = _id(state.last_message_id) if state else 0
        return _id(channel.last_message_id) > read

    def mention_count(self, channel_id: str) -> int:
        state = self.read_states.get(channel_id)
        return state.mention_count if state else 0

    def guild_badge(self, guild_id: str) -> tuple[bool, int]:
        channels = [
            channel
            for channel in self.guild_channels(guild_id, include_threads=True)
            if (not channel.is_thread or channel.joined) and self.can_view(channel.id)
        ]
        unread = any(self.is_unread(channel.id) for channel in channels)
        return unread, sum(self.mention_count(channel.id) for channel in channels)

    def dm_badge(self) -> tuple[bool, int]:
        unread = any(self.is_unread(cid) for cid in self.private_channel_ids)
        return unread, sum(self.mention_count(cid) for cid in self.private_channel_ids)

    def typing_users(self, channel_id: str, now: float | None = None) -> list[User]:
        moment = self._clock() if now is None else now
        typers = self.typing.get(channel_id, {})
        for user_id in [uid for uid, expiry in typers.items() if expiry <= moment]:
            del typers[user_id]
        return [self.users.get(uid, User(uid, uid)) for uid in typers]

    # ----- local actions ---------------------------------------------------------------------

    def set_active(self, channel_id: str | None) -> None:
        self.active_channel = channel_id
        if channel_id in self.histories:
            self.histories.move_to_end(channel_id)

    def set_history(
        self, channel_id: str, messages: Iterable[MessageInfo], *, older: bool, limit: int = 50
    ) -> list[StoreChange]:
        page = list(messages)
        history = self.histories.get(channel_id)
        if history is None or not older:
            pending = (
                [message for message in history.messages if message.pending] if history else []
            )
            history = ChannelHistory()
            known = {message.id for message in page}
            history.messages.extend(page)
            history.messages.extend(message for message in pending if message.id not in known)
            self.histories[channel_id] = history
        else:
            known = {message.id for message in history.messages}
            history.messages.extendleft(
                reversed([message for message in page if message.id not in known])
            )
        history.loaded = True
        for message in page:
            self._remember_author(message)
        history.has_more_before = len(page) >= limit
        self.histories.move_to_end(channel_id)
        self._evict()
        return [MessagesChanged(channel_id, "reset")]

    def add_pending(self, message: MessageInfo) -> list[StoreChange]:
        if message.nonce is None:
            raise ValueError("pending messages need a nonce")
        pending = replace(message, id=message.nonce, pending=True, failed=False)
        self.pending[message.nonce] = pending
        return self._upsert(pending)

    def resolve_pending(self, nonce: str, message: MessageInfo) -> list[StoreChange]:
        changes: list[StoreChange] = []
        if self.pending.pop(nonce, None) is not None:
            changes += self._remove(message.channel_id, nonce)
        return changes + self._upsert(message)

    def fail_pending(self, nonce: str) -> list[StoreChange]:
        pending = self.pending.get(nonce)
        if pending is None:
            return []
        failed = replace(pending, failed=True)
        self.pending[nonce] = failed
        return self._upsert(failed)

    def mark_read(self, channel_id: str, message_id: str) -> list[StoreChange]:
        current = self.read_states.get(channel_id)
        if current and current.last_message_id == message_id and current.mention_count == 0:
            return []
        self.read_states[channel_id] = ReadState(channel_id, message_id, 0)
        return [UnreadChanged((channel_id,))]

    # ----- gateway events --------------------------------------------------------------------

    def apply(self, event: GatewayEvent) -> list[StoreChange]:
        match event:
            case Ready(snapshot):
                return self.load_ready(snapshot)
            case MessageCreated(message):
                self._remember_author(message)
                return self._message_created(message)
            case MessageUpdated(message):
                self._remember_author(message)
                history = self.histories.get(message.channel_id)
                if history is None or history.index(message.id) is None:
                    return []
                return self._upsert(message)
            case MessageDeleted(message_id, channel_id, _):
                return self._remove(channel_id, message_id)
            case TypingStarted(channel_id, _, user_id, _):
                if self.me is not None and user_id == self.me.id:
                    return []
                self.typing.setdefault(channel_id, {})[user_id] = self._clock() + TYPING_SECONDS
                return [TypingChanged(channel_id)]
            case MessageAcked(channel_id, message_id, mention_count):
                self.read_states[channel_id] = ReadState(channel_id, message_id, mention_count or 0)
                return [UnreadChanged((channel_id,))]
            case ChannelUpserted(channel):
                self._put_channel(channel)
                return [ChannelsChanged(channel.guild_id)]
            case ChannelDeleted(channel_id, guild_id):
                self.channels.pop(channel_id, None)
                self.histories.pop(channel_id, None)
                if channel_id in self.private_channel_ids:
                    self.private_channel_ids.remove(channel_id)
                return [ChannelsChanged(guild_id)]
            case GuildUpserted(guild):
                self._put_guild(guild)
                return [GuildsChanged(), ChannelsChanged(guild.id)]
            case GuildRemoved(guild_id, unavailable):
                guild = self.guilds.get(guild_id)
                if guild is None:
                    return []
                if unavailable:
                    self.guilds[guild_id] = replace(guild, unavailable=True)
                else:
                    del self.guilds[guild_id]
                    self.members.pop(guild_id, None)
                    for channel in self.guild_channels(guild_id, include_threads=True):
                        self.channels.pop(channel.id, None)
                return [GuildsChanged(), ChannelsChanged(guild_id)]
            case ConnectionChanged(status, retry_in):
                self.connection = status
                return [ConnectionStatusChanged(status, retry_in)]
            case SessionInvalidated():
                return [SessionEnded()]
            case ThreadUpserted(thread):
                # Thread updates omit membership; it changes through ThreadMembershipChanged.
                previous = self.channels.get(thread.id)
                if previous is not None and previous.joined and not thread.joined:
                    thread = replace(thread, joined=True)
                self._put_channel(thread)
                return [ChannelsChanged(thread.guild_id)]
            case ThreadMembershipChanged(thread_id, guild_id, joined):
                thread = self.channels.get(thread_id)
                if thread is None or thread.joined == joined:
                    return []
                self.channels[thread_id] = replace(thread, joined=joined)
                return [ChannelsChanged(guild_id or thread.guild_id)]
            case ThreadDeleted(thread_id, guild_id, _):
                self.channels.pop(thread_id, None)
                self.histories.pop(thread_id, None)
                return [ChannelsChanged(guild_id)]
            case ThreadListSynced(guild_id, parent_ids, threads):
                for channel in self.guild_channels(guild_id, include_threads=True):
                    stale = channel.is_thread and not channel.joined
                    if stale and (parent_ids is None or channel.parent_id in parent_ids):
                        self.channels.pop(channel.id, None)
                for thread in threads:
                    self._put_channel(replace(thread, guild_id=thread.guild_id or guild_id))
                return [ChannelsChanged(guild_id)]
            case MembersLoaded(guild_id, members):
                own_changed = False
                for member in members:
                    own_changed |= self._remember_member(guild_id, member)
                if own_changed:  # our roles decide which channels are visible
                    return [ChannelsChanged(guild_id), MembersChanged(guild_id)]
                return [MembersChanged(guild_id)]
            case RoleUpserted(guild_id, role):
                return self._replace_roles(
                    guild_id, lambda roles: (*(r for r in roles if r.id != role.id), role)
                )
            case RoleDeleted(guild_id, role_id):
                return self._replace_roles(
                    guild_id, lambda roles: tuple(r for r in roles if r.id != role_id)
                )
            case _:
                return []

    def _replace_roles(
        self, guild_id: str, update: Callable[[tuple[Role, ...]], tuple[Role, ...]]
    ) -> list[StoreChange]:
        guild = self.guilds.get(guild_id)
        if guild is None:
            return []
        self.guilds[guild_id] = replace(guild, roles=update(guild.roles))
        self._permissions.clear()
        return [ChannelsChanged(guild_id), MembersChanged(guild_id)]

    def _message_created(self, message: MessageInfo) -> list[StoreChange]:
        changes: list[StoreChange] = []
        channel = self.channels.get(message.channel_id)
        if channel is not None and _id(message.id) > _id(channel.last_message_id):
            self.channels[channel.id] = replace(channel, last_message_id=message.id)
        typers = self.typing.get(message.channel_id)
        if typers and typers.pop(message.author.id, None) is not None:
            changes.append(TypingChanged(message.channel_id))
        own = self.me is not None and message.author.id == self.me.id
        if own:
            self.read_states[message.channel_id] = ReadState(message.channel_id, message.id, 0)
            if message.nonce is not None and message.nonce in self.pending:
                return [
                    *changes,
                    *self.resolve_pending(message.nonce, message),
                    UnreadChanged((message.channel_id,)),
                ]
        elif self._mentions_me(message, channel):
            state = self.read_states.get(message.channel_id)
            self.read_states[message.channel_id] = ReadState(
                message.channel_id,
                state.last_message_id if state else None,
                (state.mention_count if state else 0) + 1,
            )
        history = self.histories.get(message.channel_id)
        if history is not None and history.loaded:
            changes += self._upsert(message)
        changes.append(UnreadChanged((message.channel_id,)))
        return changes

    def _mentions_me(self, message: MessageInfo, channel: Channel | None) -> bool:
        if self.me is None:
            return False
        if channel is not None and channel.is_private:
            return True
        return message.mention_everyone or self.me.id in message.mention_ids

    def _upsert(self, message: MessageInfo) -> list[StoreChange]:
        history = self.histories.get(message.channel_id)
        if history is None:
            history = ChannelHistory(loaded=True)
            self.histories[message.channel_id] = history
            self._evict()
        position = history.index(message.id)
        if position is not None:
            history.messages[position] = message
            return [MessagesChanged(message.channel_id, "update", message.id)]
        history.messages.append(message)
        if len(history.messages) > self.history_limit:
            history.messages.popleft()
            history.has_more_before = True
        return [MessagesChanged(message.channel_id, "append", message.id)]

    def _remove(self, channel_id: str, message_id: str) -> list[StoreChange]:
        history = self.histories.get(channel_id)
        position = history.index(message_id) if history else None
        if history is None or position is None:
            return []
        del history.messages[position]
        return [MessagesChanged(channel_id, "remove", message_id)]

    def _evict(self) -> None:
        while len(self.histories) > self.max_cached_channels:
            for channel_id in self.histories:
                if channel_id != self.active_channel:
                    del self.histories[channel_id]
                    break
            else:
                return
