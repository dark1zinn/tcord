"""Map ``tcord_core`` native objects to the client's dataclasses."""

import tcord_core

from ..models import (
    Attachment,
    AuthFailure,
    AuthKind,
    AuthState,
    Channel,
    ChannelDeleted,
    ChannelUpserted,
    ConnectionChanged,
    ConnectionStatus,
    GatewayEvent,
    Guild,
    GuildRemoved,
    GuildUpserted,
    MessageAcked,
    MessageCreated,
    MessageDeleted,
    MessageInfo,
    MessageUpdated,
    MfaMethod,
    QrUser,
    ReadState,
    Ready,
    ReadySnapshot,
    Role,
    SessionInvalidated,
    TypingStarted,
    User,
)


def user(native: tcord_core.User) -> User:
    return User(
        id=native.id,
        username=native.username,
        global_name=native.global_name,
        bot=native.bot,
    )


def auth_state(native: tcord_core.AuthState) -> AuthState:
    qr_user = native.qr_user
    return AuthState(
        version=native.version,
        kind=AuthKind(native.kind),
        mfa_methods=tuple(MfaMethod(method) for method in native.mfa_methods),
        qr_url=native.qr_url,
        qr_expires_at=native.qr_expires_at,
        qr_user=(
            QrUser(
                id=qr_user.id,
                discriminator=qr_user.discriminator,
                avatar=qr_user.avatar,
                username=qr_user.username,
            )
            if qr_user is not None
            else None
        ),
        user=user(native.user) if native.user is not None else None,
        failure=AuthFailure(native.failure) if native.failure is not None else None,
        retry_after=native.retry_after,
        session_persisted=native.session_persisted,
    )


def channel(native: tcord_core.Channel) -> Channel:
    return Channel(
        id=native.id,
        kind=native.kind,
        name=native.name,
        guild_id=native.guild_id,
        position=native.position,
        parent_id=native.parent_id,
        last_message_id=native.last_message_id,
        topic=native.topic,
        recipients=tuple(user(recipient) for recipient in native.recipients),
    )


def guild(native: tcord_core.Guild) -> Guild:
    return Guild(
        id=native.id,
        name=native.name,
        icon=native.icon,
        channels=tuple(channel(item) for item in native.channels),
        roles=tuple(Role(id=role.id, name=role.name, color=role.color) for role in native.roles),
        unavailable=native.unavailable,
    )


def message(native: tcord_core.Message) -> MessageInfo:
    referenced = native.referenced
    return MessageInfo(
        id=native.id,
        channel_id=native.channel_id,
        guild_id=native.guild_id,
        author=user(native.author),
        author_nick=native.author_nick,
        content=native.content,
        timestamp=native.timestamp,
        edited_timestamp=native.edited_timestamp,
        kind=native.kind,
        mention_ids=tuple(native.mention_ids),
        mention_role_ids=tuple(native.mention_role_ids),
        mention_everyone=native.mention_everyone,
        attachments=tuple(
            Attachment(filename=item.filename, url=item.url, size=item.size)
            for item in native.attachments
        ),
        embed_titles=tuple(native.embed_titles),
        referenced=message(referenced) if referenced is not None else None,
        nonce=native.nonce,
    )


def ready(native: tcord_core.Ready) -> ReadySnapshot:
    return ReadySnapshot(
        user=user(native.user),
        guilds=tuple(guild(item) for item in native.guilds),
        private_channels=tuple(channel(item) for item in native.private_channels),
        read_states=tuple(
            ReadState(
                channel_id=state.channel_id,
                last_message_id=state.last_message_id,
                mention_count=state.mention_count,
            )
            for state in native.read_states
        ),
        users=tuple(user(item) for item in native.users),
    )


def event(native: tcord_core.GatewayEvent) -> GatewayEvent | None:
    """Map a native event; unknown kinds (from a newer engine) are dropped."""
    match native.kind:
        case "ready" if native.ready is not None:
            return Ready(ready(native.ready))
        case "message_create" if native.message is not None:
            return MessageCreated(message(native.message))
        case "message_update" if native.message is not None:
            return MessageUpdated(message(native.message))
        case "message_delete" if native.message_id and native.channel_id:
            return MessageDeleted(native.message_id, native.channel_id, native.guild_id)
        case "typing_start" if native.channel_id and native.user_id:
            return TypingStarted(
                native.channel_id, native.guild_id, native.user_id, native.timestamp or 0
            )
        case "message_ack" if native.channel_id and native.message_id:
            return MessageAcked(native.channel_id, native.message_id, native.mention_count)
        case "channel_upsert" if native.channel is not None:
            return ChannelUpserted(channel(native.channel))
        case "channel_delete" if native.channel_id:
            return ChannelDeleted(native.channel_id, native.guild_id)
        case "guild_upsert" if native.guild is not None:
            return GuildUpserted(guild(native.guild))
        case "guild_delete" if native.guild_id:
            return GuildRemoved(native.guild_id, bool(native.unavailable))
        case "status" if native.status is not None:
            return ConnectionChanged(ConnectionStatus(native.status), native.retry_in)
        case "session_invalidated":
            return SessionInvalidated()
    return None
