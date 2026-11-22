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
    Embed,
    EmbedMedia,
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
    MfaMethod,
    PermissionOverwrite,
    QrUser,
    ReadState,
    Ready,
    ReadySnapshot,
    Role,
    RoleDeleted,
    RoleUpserted,
    SessionInvalidated,
    Sticker,
    ThreadDeleted,
    ThreadListSynced,
    ThreadMembershipChanged,
    ThreadMetadata,
    ThreadPage,
    ThreadUpserted,
    TypingStarted,
    User,
)


def user(native: tcord_core.User) -> User:
    return User(
        id=native.id,
        username=native.username,
        global_name=native.global_name,
        bot=native.bot,
        avatar=native.avatar,
    )


def role(native: tcord_core.Role) -> Role:
    return Role(
        id=native.id,
        name=native.name,
        color=native.color,
        position=native.position,
        permissions=native.permissions,
    )


def member(native: tcord_core.Member) -> Member:
    return Member(
        user_id=native.user_id,
        nick=native.nick,
        avatar=native.avatar,
        roles=tuple(native.roles),
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
    meta = native.thread_metadata
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
        permission_overwrites=tuple(
            PermissionOverwrite(item.id, item.kind, item.allow, item.deny)
            for item in native.permission_overwrites
        ),
        owner_id=native.owner_id,
        thread_metadata=(
            ThreadMetadata(meta.archived, meta.locked, meta.archive_timestamp)
            if meta is not None
            else None
        ),
        message_count=native.message_count,
        joined=native.joined,
    )


def guild(native: tcord_core.Guild) -> Guild:
    return Guild(
        id=native.id,
        name=native.name,
        icon=native.icon,
        channels=None if native.channels is None else tuple(map(channel, native.channels)),
        roles=tuple(role(item) for item in native.roles),
        unavailable=native.unavailable,
        owner_id=native.owner_id,
        threads=None if native.threads is None else tuple(map(channel, native.threads)),
        me=member(native.me) if native.me is not None else None,
    )


def _media(native: tcord_core.EmbedMedia | None) -> EmbedMedia | None:
    if native is None:
        return None
    return EmbedMedia(native.url, native.proxy_url, native.width, native.height)


def message(native: tcord_core.Message) -> MessageInfo:
    referenced = native.referenced
    return MessageInfo(
        id=native.id,
        channel_id=native.channel_id,
        guild_id=native.guild_id,
        author=user(native.author),
        member=member(native.member) if native.member is not None else None,
        content=native.content,
        timestamp=native.timestamp,
        edited_timestamp=native.edited_timestamp,
        kind=native.kind,
        mention_ids=tuple(native.mention_ids),
        mention_role_ids=tuple(native.mention_role_ids),
        mention_everyone=native.mention_everyone,
        attachments=tuple(
            Attachment(
                filename=item.filename,
                url=item.url,
                size=item.size,
                proxy_url=item.proxy_url,
                content_type=item.content_type,
                width=item.width,
                height=item.height,
            )
            for item in native.attachments
        ),
        embeds=tuple(
            Embed(
                kind=item.kind,
                title=item.title,
                description=item.description,
                url=item.url,
                image=_media(item.image),
                thumbnail=_media(item.thumbnail),
                video=_media(item.video),
            )
            for item in native.embeds
        ),
        stickers=tuple(Sticker(item.id, item.name, item.format) for item in native.stickers),
        referenced=message(referenced) if referenced is not None else None,
        thread=channel(native.thread) if native.thread is not None else None,
        nonce=native.nonce,
    )


def thread_page(native: tcord_core.ThreadPage) -> ThreadPage:
    return ThreadPage(tuple(channel(item) for item in native.threads), native.has_more)


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
        case "thread_upsert" if native.channel is not None:
            return ThreadUpserted(channel(native.channel))
        case "thread_delete" if native.channel_id:
            return ThreadDeleted(native.channel_id, native.guild_id, native.parent_id)
        case "thread_membership" if native.channel_id and native.joined is not None:
            return ThreadMembershipChanged(native.channel_id, native.guild_id, native.joined)
        case "thread_list_sync" if native.guild_id and native.threads is not None:
            ids = tuple(native.channel_ids) if native.channel_ids is not None else None
            return ThreadListSynced(
                native.guild_id, ids, tuple(channel(item) for item in native.threads)
            )
        case "members" if native.guild_id and native.members is not None:
            return MembersLoaded(native.guild_id, tuple(member(item) for item in native.members))
        case "role_upsert" if native.guild_id and native.role is not None:
            return RoleUpserted(native.guild_id, role(native.role))
        case "role_delete" if native.guild_id and native.role_id:
            return RoleDeleted(native.guild_id, native.role_id)
    return None
