"""Images and member data in the chat UI: icons, rail badges, avatars and name colours."""

from __future__ import annotations

import io
from dataclasses import replace

from client.cdn import attachment_preview_url, avatar_url, emoji_url, guild_icon_url, sticker_url
from client.models import (
    Attachment,
    Embed,
    EmbedMedia,
    Member,
    MembersLoaded,
    MessageCreated,
    MessageInfo,
    Role,
    Sticker,
    StickerFormat,
)
from conftest import settle, wait_until
from fakes import FRIEND, GENERAL, GUILD_ID, ME, OTHER, RANDOM, FakeClient, make_message
from PIL import Image
from rich.console import Console
from rich.padding import Padding
from rich.style import Style
from rich.text import Text
from tcord.app import TcordApp
from tcord.images import PLACEHOLDER, fit
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


def gif(frames: int) -> bytes:
    images = [Image.new("RGB", (8, 8), (i * 80, 0, 0)) for i in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="GIF", save_all=True, append_images=images[1:], duration=40)
    return buffer.getvalue()


def show_message(client: FakeClient, message: MessageInfo, settings: Settings) -> TcordApp:
    """An app whose #general history ends with ``message``."""
    client.messages.histories[GENERAL].append(message)
    return TcordApp(client, settings)


async def test_image_attachment_sticker_and_jumbo_emoji_render_as_images() -> None:
    client = FakeClient()
    attachment = Attachment(
        "cat.png",
        "https://cdn.discordapp.com/attachments/1/2/cat.png",
        10,
        proxy_url="https://media.discordapp.net/attachments/1/2/cat.png",
        content_type="image/png",
        width=200,
        height=100,
    )
    sticker = Sticker("77", "wave", StickerFormat.PNG)
    message = make_message(31, author=FRIEND, content="<:blob:55> <:blob:56>")
    message = replace(message, attachments=(attachment,), stickers=(sticker,))
    cols, rows = fit(200, 100, 40, 15)
    client.media.files[attachment_preview_url(attachment, cols * 10, rows * 20)] = png((1, 2, 3))
    client.media.files[sticker_url(sticker, 160) or ""] = png((4, 5, 6))
    for emoji_id in ("55", "56"):
        client.media.files[emoji_url(emoji_id, animated=False, pixels=48)] = png((7, 8, 9))
    app = show_message(client, message, HALFBLOCK)
    async with app.run_test(size=(120, 40)) as pilot:
        _, messages = await open_general_with(app, pilot, 31)

        def text() -> str:
            return prompt_text(messages, message.id)

        await wait_until(pilot, lambda: text().count("▀▀▀▀ ▀▀▀▀") >= 1 and "[sticker" not in text())
        rendered = text()
        assert "<:blob" not in rendered  # jumbo emoji replace the codes
        assert ":blob:" not in rendered
        assert "cat.png" not in rendered  # the preview replaces the file line
        assert sum(1 for line in rendered.splitlines() if line.strip().startswith("▀" * 16)) >= 8


async def test_missing_media_falls_back_to_text() -> None:
    client = FakeClient()
    attachment = Attachment(
        "notes.txt", "https://cdn.discordapp.com/attachments/1/2/notes.txt", 2048
    )
    lottie = Sticker("78", "dance", StickerFormat.LOTTIE)
    message = replace(
        make_message(31, author=FRIEND, content="hi <:gone:9>"),
        attachments=(attachment,),
        stickers=(lottie,),
    )
    app = show_message(client, message, HALFBLOCK)
    async with app.run_test(size=(120, 40)) as pilot:
        _, messages = await open_general_with(app, pilot, 31)
        await settle(pilot, 10)
        rendered = prompt_text(messages, message.id)
        assert "hi :gone:" in rendered  # half-blocks are too coarse for inline emoji
        assert "notes.txt (2.0 KB)" in rendered
        assert "[sticker: dance]" in rendered


async def test_gif_link_is_replaced_by_its_preview() -> None:
    client = FakeClient()
    preview = EmbedMedia(
        "https://media.tenor.com/x.png",
        proxy_url="https://images-ext-1.discordapp.net/external/x.png",
        width=100,
        height=100,
    )
    embed = Embed(kind="gifv", url="https://tenor.com/view/cat", thumbnail=preview)
    message = replace(
        make_message(31, author=FRIEND, content="https://tenor.com/view/cat"), embeds=(embed,)
    )
    client.media.files[preview.proxy_url or ""] = png((9, 9, 9))
    app = show_message(client, message, HALFBLOCK)
    async with app.run_test(size=(120, 40)) as pilot:
        _, messages = await open_general_with(app, pilot, 31)
        await wait_until(pilot, lambda: "▀" in prompt_text(messages, message.id))
        assert "tenor.com" not in prompt_text(messages, message.id)


async def test_highlighted_gif_animates() -> None:
    client = FakeClient()
    attachment = Attachment(
        "a.gif",
        "https://cdn.discordapp.com/attachments/1/2/a.gif",
        10,
        content_type="image/gif",
        width=8,
        height=8,
    )
    message = replace(make_message(31, author=FRIEND, content=""), attachments=(attachment,))
    client.media.files[attachment.url] = gif(3)
    settings = Settings(appearance=AppearanceSettings(image_protocol="halfblock"))
    app = show_message(client, message, settings)
    async with app.run_test(size=(120, 40)) as pilot:
        _, messages = await open_general_with(app, pilot, 31)
        messages.focus()
        messages.highlighted = messages.get_option_index(f"m{message.id}")
        seen: set[str] = set()

        def colours() -> bool:
            prompt = messages.get_option(f"m{message.id}").prompt
            console = Console(width=80, record=True, color_system="truecolor")
            console.print(prompt)
            seen.add(console.export_text(styles=True))
            return len(seen) >= 2

        await wait_until(pilot, colours, rounds=200)


async def test_kitty_renders_inline_custom_emoji() -> None:
    client = FakeClient()
    message = make_message(31, author=FRIEND, content="hello <:wave:12> there")
    client.media.files[emoji_url("12", animated=False, pixels=48)] = png((1, 1, 1))
    settings = Settings(appearance=AppearanceSettings(image_protocol="kitty"))
    app = show_message(client, message, settings)
    async with app.run_test(size=(120, 40)) as pilot:
        _, messages = await open_general_with(app, pilot, 31)
        await wait_until(pilot, lambda: PLACEHOLDER in prompt_text(messages, message.id))
        assert ":wave:" not in prompt_text(messages, message.id)


async def open_general_with(
    app: TcordApp, pilot: Pilot, count: int
) -> tuple[ChatScreen, MessageList]:
    chat = await chat_screen(app, pilot)
    chat.choose_guild(GUILD_ID)
    chat.open_channel(GENERAL)
    messages = chat.query_one(MessageList)
    await wait_until(pilot, lambda: len(messages.messages) == count)
    await settle(pilot)
    return chat, messages
