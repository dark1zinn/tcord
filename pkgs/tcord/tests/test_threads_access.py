"""Threads, forum posts and permission-aware channel visibility in the channel tree."""

from __future__ import annotations

from dataclasses import replace

from client.models import Channel, ChannelKind, Member, PermissionOverwrite, Role, ThreadUpserted
from conftest import settle, wait_until
from fakes import GENERAL, GUILD_ID, ME, RANDOM, FakeClient, make_message
from tcord.app import TcordApp
from tcord.screens.chat import ChatScreen
from tcord.settings import LayoutSettings, Settings
from tcord.widgets.channels import ChannelTree
from tcord.widgets.messages import MessageList
from textual.pilot import Pilot
from textual.widgets.tree import TreeNode

THREAD = "500000000000000001"
FORUM = "500000000000000002"
POST = "500000000000000003"
VIEW_CHANNEL = 1 << 10


def thread(thread_id: str, parent: str, name: str) -> Channel:
    return Channel(thread_id, ChannelKind.PUBLIC_THREAD, name, GUILD_ID, parent_id=parent)


def with_access_rules(client: FakeClient) -> None:
    """@everyone can view; #random denies VIEW_CHANNEL to @everyone; add a forum."""
    guild = client.gateway.snapshot.guilds[0]
    channels = []
    for channel in guild.channels:
        if channel.id == RANDOM:
            deny = PermissionOverwrite(GUILD_ID, 0, 0, VIEW_CHANNEL)
            channel = replace(channel, permission_overwrites=(deny,))
        channels.append(channel)
    forum = Channel(FORUM, ChannelKind.FORUM, "help", GUILD_ID, position=5)
    guild = replace(
        guild,
        channels=(*channels, forum),
        roles=(Role(GUILD_ID, "@everyone", 0, 0, VIEW_CHANNEL | (1 << 11)),),
        me=Member(ME.id),
        owner_id="999",
    )
    client.gateway.snapshot = replace(client.gateway.snapshot, guilds=(guild,))


async def guild_tree(app: TcordApp, pilot: Pilot) -> tuple[ChatScreen, ChannelTree]:
    await wait_until(pilot, lambda: isinstance(app.screen, ChatScreen))
    chat = app.screen
    assert isinstance(chat, ChatScreen)
    chat.choose_guild(GUILD_ID)
    await settle(pilot)
    return chat, chat.query_one(ChannelTree)


def labels(tree: ChannelTree) -> list[str]:
    found: list[str] = []

    def walk(node: TreeNode[str]) -> None:
        for child in node.children:
            found.append(str(child.label))
            walk(child)

    walk(tree.root)
    return found


async def test_inaccessible_channels_hidden_by_default() -> None:
    client = FakeClient()
    with_access_rules(client)
    app = TcordApp(client)
    async with app.run_test(size=(120, 36)) as pilot:
        _, tree = await guild_tree(app, pilot)
        assert not any("random" in label for label in labels(tree))
        assert RANDOM not in tree.visible_channels()


async def test_inaccessible_channels_listed_locked_when_enabled() -> None:
    client = FakeClient()
    with_access_rules(client)
    settings = Settings(layout=LayoutSettings(show_inaccessible_channels=True))
    app = TcordApp(client, settings)
    async with app.run_test(size=(120, 36)) as pilot:
        chat, tree = await guild_tree(app, pilot)
        locked = next(label for label in labels(tree) if "random" in label)
        assert "🔒" in locked
        assert RANDOM not in tree.visible_channels()  # navigation skips it
        tree.highlight_channel(RANDOM)
        tree.focus()
        await pilot.press("enter")
        await settle(pilot)
        assert chat.active_channel != RANDOM
        assert (RANDOM, None) not in client.messages.history_calls


async def test_active_threads_nest_under_parent_and_open() -> None:
    client = FakeClient()
    client.messages.histories[THREAD] = [make_message(1, THREAD)]
    app = TcordApp(client)
    async with app.run_test(size=(120, 36)) as pilot:
        chat, tree = await guild_tree(app, pilot)
        client.gateway.push(ThreadUpserted(thread(THREAD, GENERAL, "release-plan")))
        await wait_until(pilot, lambda: any("release-plan" in label for label in labels(tree)))
        general = tree._channel_nodes[GENERAL]
        assert [child.data for child in general.children] == [THREAD]
        assert tree.visible_channels()[:2] == [GENERAL, THREAD]
        tree.highlight_channel(THREAD)
        tree.focus()
        await pilot.press("enter")
        await wait_until(pilot, lambda: chat.active_channel == THREAD)
        assert (THREAD, None) in client.messages.history_calls


async def test_forum_posts_load_when_expanded() -> None:
    client = FakeClient()
    with_access_rules(client)
    client.gateway.thread_pages[FORUM] = [thread(POST, FORUM, "How do I log in?")]
    app = TcordApp(client)
    async with app.run_test(size=(120, 36)) as pilot:
        _, tree = await guild_tree(app, pilot)
        assert any("help" in label for label in labels(tree))
        assert client.gateway.thread_calls == []
        tree.highlight_channel(FORUM)
        tree.focus()
        await pilot.press("enter")  # toggles the forum open
        await wait_until(pilot, lambda: any("How do I log in?" in lab for lab in labels(tree)))
        assert client.gateway.thread_calls == [FORUM]


async def test_enter_on_thread_starter_opens_the_thread() -> None:
    client = FakeClient()
    started = replace(make_message(31), thread=thread(THREAD, GENERAL, "side-topic"))
    client.messages.histories[GENERAL].append(started)
    client.messages.histories[THREAD] = [make_message(1, THREAD)]
    app = TcordApp(client)
    async with app.run_test(size=(120, 36)) as pilot:
        chat, _ = await guild_tree(app, pilot)
        chat.open_channel(GENERAL)
        messages = chat.query_one(MessageList)
        await wait_until(pilot, lambda: len(messages.messages) == 31)
        messages.focus()
        messages.highlighted = messages.get_option_index(f"m{started.id}")
        await pilot.press("enter")
        await wait_until(pilot, lambda: chat.active_channel == THREAD)
