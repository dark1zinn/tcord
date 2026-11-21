"""Images and member data in the chat UI: icons, rail badges, avatars and name colours."""

from __future__ import annotations

import io
from dataclasses import replace

from client.cdn import avatar_url, guild_icon_url
from client.models import Member, MembersLoaded, MessageCreated, Role
from conftest import settle, wait_until
from fakes import FRIEND, GENERAL, GUILD_ID, ME, OTHER, RANDOM, FakeClient, make_message
from PIL import Image
from rich.console import Console
from rich.padding import Padding
from rich.style import Style
from rich.text import Text
from tcord.app import TcordApp
from tcord.screens.chat import ChatScreen
from tcord.settings import AppearanceSettings, Settings
from tcord.widgets.messages import MessageList
from tcord.widgets.rail import ServerRail
from textual.pilot import Pilot

HALFBLOCK = Settings(appearance=AppearanceSettings(image_protocol="halfblock"))


def png(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format="PNG")
    return buffer.getvalue()


def with_guild_icon(client: FakeClient) -> str:
    guild = client.gateway.snapshot.guilds[0]
    client.gateway.snapshot = replace(
        client.gateway.snapshot, guilds=(replace(guild, icon="iconhash"),)
    )
    url = guild_icon_url(GUILD_ID, "iconhash", 64)
    client.media.files[url] = png((200, 10, 10))
    return url


def rail_prompt(chat: ChatScreen) -> str:
    rail = chat.query_one(ServerRail)
    return str(rail.get_option(f"g{GUILD_ID}").prompt)


async def chat_screen(app: TcordApp, pilot: Pilot) -> ChatScreen:
    await wait_until(pilot, lambda: isinstance(app.screen, ChatScreen))
    await settle(pilot)
    screen = app.screen
    assert isinstance(screen, ChatScreen)
    return screen


async def test_rail_draws_guild_icon_once_loaded() -> None:
    client = FakeClient()
    url = with_guild_icon(client)
    app = TcordApp(client, HALFBLOCK)
    async with app.run_test(size=(120, 36)) as pilot:
        chat = await chat_screen(app, pilot)
        await wait_until(pilot, lambda: "▀" in rail_prompt(chat))
        lines = rail_prompt(chat).split("\n")
        assert len(lines) == 3
        assert all(line[1:7] == "▀" * 6 for line in lines)
    assert client.media.requests.count(url) == 1


async def test_rail_falls_back_to_initials_without_images() -> None:
    client = FakeClient()
    with_guild_icon(client)
    app = TcordApp(client, Settings(appearance=AppearanceSettings(image_protocol="off")))
    async with app.run_test(size=(120, 36)) as pilot:
        chat = await chat_screen(app, pilot)
        assert "TT" in rail_prompt(chat)
    assert client.media.requests == []


async def test_rail_unread_pill_and_mention_badge() -> None:
    client = FakeClient()
    app = TcordApp(client, HALFBLOCK)
    async with app.run_test(size=(120, 36)) as pilot:
        chat = await chat_screen(app, pilot)
        assert "▌" not in rail_prompt(chat)
        mention = make_message(60, RANDOM, OTHER, f"<@{ME.id}>", mention_ids=(ME.id,))
        client.gateway.push(MessageCreated(mention))
        await wait_until(pilot, lambda: " 1 " in rail_prompt(chat))
        lines = rail_prompt(chat).split("\n")
        assert lines[1].startswith("▌")  # short pill on the middle row: unread
        assert not lines[0].startswith("▌")
        assert lines[2].endswith(" 1 ")  # badge in the bottom-right corner

        chat.choose_guild(GUILD_ID)
        await settle(pilot)
        lines = rail_prompt(chat).split("\n")
        assert all(line.startswith("▌") for line in lines)  # tall pill: selected


async def open_general(app: TcordApp, pilot: Pilot) -> tuple[ChatScreen, MessageList]:
    chat = await chat_screen(app, pilot)
    chat.choose_guild(GUILD_ID)
    chat.open_channel(GENERAL)
    messages = chat.query_one(MessageList)
    await wait_until(pilot, lambda: len(messages.messages) == 30)
    await settle(pilot)
    return chat, messages


def prompt_text(messages: MessageList, message_id: str) -> str:
    """Plain text of a rendered message option, as the terminal would show it."""
    option = messages.get_option(f"m{message_id}")
    console = Console(width=80, record=True, color_system=None)
    console.print(option.prompt)
    return "\n".join(line.rstrip() for line in console.export_text().splitlines())


async def test_avatars_render_beside_group_headers_only() -> None:
    client = FakeClient()
    for user in (ME, FRIEND):
        client.media.files[avatar_url(user, None, GUILD_ID, 64)] = png((0, 200, 0))
    app = TcordApp(client, HALFBLOCK)
    async with app.run_test(size=(120, 36)) as pilot:
        _, messages = await open_general(app, pilot)
        header = make_message(30)  # authored by Me after Friend: starts a group
        await wait_until(pilot, lambda: "▀▀▀▀" in prompt_text(messages, header.id))
        lines = prompt_text(messages, header.id).splitlines()
        assert lines[0] == ""  # one blank line separates message groups
        assert lines[1].startswith("▀▀▀▀ Me")


async def test_avatar_column_hidden_when_disabled() -> None:
    client = FakeClient()
    settings = Settings(
        appearance=AppearanceSettings(image_protocol="halfblock", show_avatars=False)
    )
    app = TcordApp(client, settings)
    async with app.run_test(size=(120, 36)) as pilot:
        _, messages = await open_general(app, pilot)
        lines = prompt_text(messages, make_message(30).id).splitlines()
        assert lines[1].startswith("Me")
    assert client.media.requests == [] or all("avatars" not in url for url in client.media.requests)


async def test_author_names_use_highest_coloured_role_and_members_are_requested() -> None:
    client = FakeClient()
    guild = client.gateway.snapshot.guilds[0]
    roles = (Role("7", "Mods", 0x00FF00, 5), Role("8", "VIP", 0xFF0000, 2))
    client.gateway.snapshot = replace(
        client.gateway.snapshot, guilds=(replace(guild, roles=roles),)
    )
    app = TcordApp(client, Settings(appearance=AppearanceSettings(image_protocol="off")))
    async with app.run_test(size=(120, 36)) as pilot:
        _, messages = await open_general(app, pilot)
        assert client.gateway.member_requests
        guild_id, requested = client.gateway.member_requests[0]
        assert guild_id == GUILD_ID
        assert set(requested) == {ME.id, FRIEND.id}
        client.gateway.push(MembersLoaded(GUILD_ID, (Member(FRIEND.id, roles=("7", "8")),)))
        friend_message = make_message(29)

        def name_colour() -> str | None:
            prompt = messages.get_option(f"m{friend_message.id}").prompt
            text = prompt.renderable if isinstance(prompt, Padding) else prompt
            assert isinstance(text, Text)
            span = next(
                span for span in text.spans if text.plain[span.start : span.end] == "Friend"
            )
            style = span.style
            return style.color.name if isinstance(style, Style) and style.color else None

        await wait_until(pilot, lambda: name_colour() == "#00ff00")
        assert len(client.gateway.member_requests) == 1  # no duplicate requests
