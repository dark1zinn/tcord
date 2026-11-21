"""Images in the chat UI: server icons, rail badges (and later avatars and attachments)."""

from __future__ import annotations

import io
from dataclasses import replace

from client.cdn import guild_icon_url
from client.models import MessageCreated
from conftest import settle, wait_until
from fakes import GUILD_ID, ME, OTHER, RANDOM, FakeClient, make_message
from PIL import Image
from tcord.app import TcordApp
from tcord.screens.chat import ChatScreen
from tcord.settings import AppearanceSettings, Settings
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
