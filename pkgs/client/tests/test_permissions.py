from dataclasses import replace
from datetime import UTC, datetime

import pytest
from client.models import (
    Channel,
    ChannelKind,
    ChannelUpserted,
    Guild,
    GuildUpserted,
    Member,
    MembersLoaded,
    MessageCreated,
    MessageInfo,
    PermissionOverwrite,
    ReadySnapshot,
    Role,
    RoleUpserted,
    ThreadListSynced,
    ThreadMembershipChanged,
    ThreadMetadata,
    ThreadUpserted,
    User,
)
from client.permissions import (
    ADMINISTRATOR,
    ALL,
    MANAGE_THREADS,
    SEND_MESSAGES,
    VIEW_CHANNEL,
    channel_permissions,
)
from client.state import ChannelsChanged, ChatStore, MembersChanged

ME = "1"
GUILD = "100"
MODS = "7"
VIP = "8"


def guild(
    *channels: Channel, everyone: int = VIEW_CHANNEL | SEND_MESSAGES, owner: str = "9"
) -> Guild:
    return Guild(
        GUILD,
        "Guild",
        channels=channels,
        roles=(
            Role(GUILD, "@everyone", 0, 0, everyone),
            Role(MODS, "Mods", 0x00FF00, 5, 0),
            Role(VIP, "VIP", 0xFF0000, 2, 0),
        ),
        owner_id=owner,
        me=Member(ME, roles=(MODS,)),
    )


def overwrite(target: str, *, kind: int = 0, allow: int = 0, deny: int = 0) -> PermissionOverwrite:
    return PermissionOverwrite(target, kind, allow, deny)


def text(channel_id: str, *overwrites: PermissionOverwrite, parent: str | None = None) -> Channel:
    return Channel(
        channel_id,
        ChannelKind.TEXT,
        f"c{channel_id}",
        GUILD,
        parent_id=parent,
        permission_overwrites=overwrites,
    )


@pytest.mark.parametrize(
    ("overwrites", "roles", "visible"),
    [
        ((), (MODS,), True),
        ((overwrite(GUILD, deny=VIEW_CHANNEL),), (MODS,), False),
        ((overwrite(GUILD, deny=VIEW_CHANNEL), overwrite(MODS, allow=VIEW_CHANNEL)), (MODS,), True),
        # Role allows win over role denies only when coming from another role's allow.
        (
            (overwrite(MODS, deny=VIEW_CHANNEL), overwrite(VIP, allow=VIEW_CHANNEL)),
            (MODS, VIP),
            True,
        ),
        # A member overwrite beats role overwrites.
        (
            (overwrite(MODS, allow=VIEW_CHANNEL), overwrite(ME, kind=1, deny=VIEW_CHANNEL)),
            (MODS,),
            False,
        ),
    ],
)
def test_overwrite_order(overwrites: tuple, roles: tuple[str, ...], visible: bool) -> None:
    channel = text("10", *overwrites)
    result = channel_permissions(guild(channel), channel, ME, roles)
    assert bool(result & VIEW_CHANNEL) is visible


def test_owner_and_administrator_see_everything() -> None:
    hidden = text("10", overwrite(GUILD, deny=VIEW_CHANNEL))
    assert channel_permissions(guild(hidden, owner=ME), hidden, ME, ()) == ALL
    admin = guild(hidden, everyone=ADMINISTRATOR)
    assert channel_permissions(admin, hidden, ME, ()) == ALL


def test_denied_view_grants_nothing() -> None:
    channel = text("10", overwrite(GUILD, deny=VIEW_CHANNEL))
    assert channel_permissions(guild(channel), channel, ME, ()) == 0


def store_with(*channels: Channel, threads: tuple[Channel, ...] = ()) -> ChatStore:
    chat = ChatStore()
    chat.load_ready(
        ReadySnapshot(
            user=User(ME, "me"),
            guilds=(replace(guild(*channels), threads=threads),),
            private_channels=(),
            read_states=(),
            users=(),
        )
    )
    return chat


def test_store_visibility_threads_follow_parent() -> None:
    secret = text("10", overwrite(GUILD, deny=VIEW_CHANNEL))
    thread = Channel("30", ChannelKind.PUBLIC_THREAD, "t", GUILD, parent_id="10")
    chat = store_with(secret, text("11"), threads=(thread,))
    assert not chat.can_view("10")
    assert not chat.can_view("30")
    assert chat.can_view("11")
    assert chat.can_view("unknown")


def test_own_role_change_reveals_channels_and_rebuilds_the_tree() -> None:
    secret = text("10", overwrite(GUILD, deny=VIEW_CHANNEL), overwrite(VIP, allow=VIEW_CHANNEL))
    thread = Channel("30", ChannelKind.PUBLIC_THREAD, "t", GUILD, parent_id="10")
    chat = store_with(secret, threads=(thread,))
    assert not chat.can_view("30")
    changes = chat.apply(MembersLoaded(GUILD, (Member(ME, roles=(MODS, VIP)),)))
    assert ChannelsChanged(GUILD) in changes
    assert chat.can_view("10")
    assert chat.can_view("30")
    # Someone else's roles do not touch the channel list.
    other = chat.apply(MembersLoaded(GUILD, (Member("2", roles=(VIP,)),)))
    assert ChannelsChanged(GUILD) not in other


def test_parent_overwrite_change_updates_thread_access() -> None:
    thread = Channel("30", ChannelKind.PUBLIC_THREAD, "t", GUILD, parent_id="10")
    chat = store_with(text("10"), threads=(thread,))
    assert chat.can_view("30")
    chat.apply(ChannelUpserted(text("10", overwrite(GUILD, deny=VIEW_CHANNEL))))
    assert not chat.can_view("30")


def test_private_threads_need_membership_or_manage_threads() -> None:
    private = Channel("31", ChannelKind.PRIVATE_THREAD, "p", GUILD, parent_id="10")
    chat = store_with(text("10"), threads=(private,))
    assert not chat.can_view("31")  # the parent is visible, the private thread is not
    chat.apply(ThreadMembershipChanged("31", GUILD, joined=True))
    assert chat.can_view("31")
    chat.apply(ThreadMembershipChanged("31", GUILD, joined=False))
    assert not chat.can_view("31")

    managers = Role(MODS, "Mods", 0x00FF00, 5, MANAGE_THREADS)
    chat.apply(RoleUpserted(GUILD, managers))  # the signed-in member has MODS
    assert chat.can_view("31")


def test_inaccessible_unread_does_not_badge_the_guild() -> None:
    secret = replace(text("10", overwrite(GUILD, deny=VIEW_CHANNEL)), last_message_id="5")
    chat = store_with(secret)
    assert chat.is_unread("10")
    assert chat.guild_badge(GUILD) == (False, 0)


def test_role_colour_uses_highest_coloured_role() -> None:
    chat = store_with(text("10"))
    chat.apply(MembersLoaded(GUILD, (Member("2", roles=(VIP, MODS)),)))
    assert chat.role_color(GUILD, "2") == 0x00FF00
    changes = chat.apply(RoleUpserted(GUILD, Role(VIP, "VIP", 0xFF0000, 9, 0)))
    assert MembersChanged(GUILD) in changes
    assert chat.role_color(GUILD, "2") == 0xFF0000
    assert chat.role_color(GUILD, "unknown") is None


def test_message_members_are_cached_and_requests_deduplicated() -> None:
    chat = store_with(text("10"))
    message = MessageInfo(
        "50",
        "10",
        User("2", "friend"),
        "hi",
        datetime(2026, 1, 1, tzinfo=UTC),
        guild_id=GUILD,
        member=Member("2", nick="Pal", roles=(VIP,)),
    )
    chat.apply(MessageCreated(message))
    assert chat.member(GUILD, "2") == Member("2", nick="Pal", roles=(VIP,))
    assert chat.missing_members(GUILD, ["2", "3", "3"]) == ["3"]
    assert chat.missing_members(GUILD, ["3"]) == []


def test_thread_tracking() -> None:
    chat = store_with(text("10"))
    active = Channel(
        "30", ChannelKind.PUBLIC_THREAD, "a", GUILD, parent_id="10", last_message_id="9"
    )
    archived = replace(
        active, id="31", thread_metadata=ThreadMetadata(archived=True), last_message_id="8"
    )
    joined = replace(archived, id="32", joined=True, last_message_id="7")
    for thread in (active, archived, joined):
        chat.apply(ThreadUpserted(thread))
    assert [thread.id for thread in chat.thread_children("10")] == ["30", "32"]
    assert "30" not in {channel.id for channel in chat.guild_channels(GUILD)}

    chat.apply(ThreadListSynced(GUILD, ("10",), ()))
    assert [thread.id for thread in chat.thread_children("10")] == ["32"]


def test_thread_membership_survives_updates_and_follows_membership_events() -> None:
    chat = store_with(text("10"))
    archived = ThreadMetadata(archived=True)
    thread = Channel("30", ChannelKind.PUBLIC_THREAD, "a", GUILD, parent_id="10", joined=True)
    chat.apply(ThreadUpserted(thread))
    # THREAD_UPDATE payloads omit the membership: archiving keeps the joined thread listed.
    chat.apply(ThreadUpserted(replace(thread, joined=False, thread_metadata=archived)))
    assert [child.id for child in chat.thread_children("10")] == ["30"]
    left = chat.apply(ThreadMembershipChanged("30", GUILD, joined=False))
    assert left == [ChannelsChanged(GUILD)]
    assert chat.thread_children("10") == []
    assert chat.apply(ThreadMembershipChanged("30", GUILD, joined=False)) == []


def test_partial_guild_update_keeps_channels() -> None:
    chat = store_with(text("10"))
    chat.apply(GuildUpserted(Guild(GUILD, "Renamed")))
    assert chat.guilds[GUILD].name == "Renamed"
    assert "10" in chat.channels
    assert chat.guilds[GUILD].me is not None
