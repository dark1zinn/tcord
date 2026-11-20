from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from client.models import MessageCreated, TypingStarted
from conftest import settle, wait_until
from fakes import (
    DM,
    FRIEND,
    GENERAL,
    GUILD_ID,
    ME,
    MFA_CODE,
    OTHER,
    RANDOM,
    FakeClient,
    make_message,
)
from tcord.app import TcordApp
from tcord.screens.chat import ChatScreen
from tcord.screens.confirm import ConfirmScreen
from tcord.screens.login import LoginScreen
from tcord.screens.settings import SettingsScreen
from tcord.settings import NavigationSettings, Settings, SettingsFile
from tcord.widgets.channels import ChannelTree
from tcord.widgets.composer import Composer
from tcord.widgets.messages import MessageList
from tcord.widgets.rail import ServerRail
from tcord.widgets.status import StatusBar
from textual.command import CommandPalette
from textual.pilot import Pilot
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Select, Static


def settings_for(preset: str = "standard") -> Settings:
    return Settings(navigation=NavigationSettings(preset=preset))


@asynccontextmanager
async def running(
    client: FakeClient,
    *,
    preset: str = "standard",
    size: tuple[int, int] = (120, 36),
    tmp_path: Path | None = None,
) -> AsyncGenerator[tuple[TcordApp, Pilot]]:
    file = SettingsFile((tmp_path or Path("/nonexistent")) / "settings.toml")
    app = TcordApp(client, settings_for(preset), file)
    async with app.run_test(size=size) as pilot:
        yield app, pilot


async def in_chat(app: TcordApp, pilot: Pilot) -> ChatScreen:
    await wait_until(pilot, lambda: isinstance(app.screen, ChatScreen))
    await settle(pilot)
    screen = app.screen
    assert isinstance(screen, ChatScreen)
    return screen


async def open_general(app: TcordApp, pilot: Pilot) -> ChatScreen:
    chat = await in_chat(app, pilot)
    chat.choose_guild(GUILD_ID)
    await settle(pilot)
    chat.open_channel(GENERAL)
    await wait_until(pilot, lambda: len(chat.query_one(MessageList).messages) == 30)
    await settle(pilot)
    return chat


def text_of(widget: Widget) -> str:
    return str(widget.render())


# (a) ------------------------------------------------------------------------------------------


async def test_login_with_mfa_wrong_then_right_code() -> None:
    client = FakeClient(stored_session=False, mfa=True)
    async with running(client) as (app, pilot):
        await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen))
        login = app.screen
        await wait_until(
            pilot,
            lambda: login.query_one("#login-switcher", ContentSwitcher).current == "login-tabs",
        )
        await pilot.click("#login")
        await pilot.press(*"me@example.com")
        await pilot.click("#password")
        await pilot.press(*"hunter2", "enter")
        await wait_until(
            pilot, lambda: login.query_one("#email-switcher", ContentSwitcher).current == "mfa"
        )
        await pilot.click("#code")
        await pilot.press(*"000000", "enter")
        await wait_until(pilot, lambda: "Invalid code" in text_of(login.query_one("#login-error")))
        await pilot.click("#code")
        await pilot.press(*MFA_CODE, "enter")
        await in_chat(app, pilot)
    assert client.auth.calls[-2:] == ["mfa:totp:000000", f"mfa:totp:{MFA_CODE}"]


async def test_empty_credentials_are_validated_locally() -> None:
    client = FakeClient(stored_session=False)
    async with running(client) as (app, pilot):
        await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen))
        await settle(pilot)
        await pilot.click("#submit")
        await settle(pilot)
        assert "Enter your email" in text_of(app.screen.query_one("#login-error"))
    assert not any(call.startswith("login:") for call in client.auth.calls)


# (b) ------------------------------------------------------------------------------------------


async def test_qr_tab_flow_and_cancel_on_leave() -> None:
    client = FakeClient(stored_session=False)
    async with running(client) as (app, pilot):
        await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen))
        login = app.screen
        await settle(pilot)
        await pilot.click("#--content-tab-tab-qr")
        await wait_until(pilot, lambda: "qr:start" in client.auth.calls)
        status = login.query_one("#qr-status", Static)
        await wait_until(pilot, lambda: "Generating" in text_of(status))
        client.auth.qr_ready()
        await wait_until(pilot, lambda: "Scan with" in text_of(status))
        assert "█" in text_of(login.query_one("#qr-code", Static)) or "▀" in text_of(
            login.query_one("#qr-code", Static)
        )
        client.auth.qr_scanned()
        await wait_until(pilot, lambda: "logging in as scanner" in text_of(status))
        await pilot.click("#--content-tab-tab-email")
        await wait_until(pilot, lambda: "qr:cancel" in client.auth.calls)


# (c) (d) --------------------------------------------------------------------------------------


async def test_ready_renders_and_history_loads_once_per_open() -> None:
    client = FakeClient()
    async with running(client) as (app, pilot):
        chat = await in_chat(app, pilot)
        rail = chat.query_one(ServerRail)
        assert rail.option_count == 2
        rail.focus()
        await pilot.press("down", "enter")
        await settle(pilot)
        assert client.gateway.subscribed == [GUILD_ID]
        tree = chat.query_one(ChannelTree)
        assert tree.visible_channels() == [GENERAL, RANDOM]
        tree.focus()
        await pilot.press("down", "down", "down")  # highlight only
        await settle(pilot)
        assert client.messages.history_calls == []
        tree.highlight_channel(GENERAL)
        await pilot.press("enter")
        await wait_until(pilot, lambda: len(chat.query_one(MessageList).messages) == 30)
        assert client.messages.history_calls == [(GENERAL, None)]
        chat.open_channel(RANDOM)
        await settle(pilot)
        chat.open_channel(GENERAL)  # cached: no refetch
        await settle(pilot)
        assert client.messages.history_calls == [(GENERAL, None), (RANDOM, None)]


async def test_scrolling_to_top_pages_older_history() -> None:
    client = FakeClient()
    client.messages.histories[GENERAL] = [make_message(i) for i in range(1, 81)]
    async with running(client) as (app, pilot):
        chat = await in_chat(app, pilot)
        chat.choose_guild(GUILD_ID)
        chat.open_channel(GENERAL)
        messages = chat.query_one(MessageList)
        await wait_until(pilot, lambda: len(messages.messages) == 50)
        messages.focus()
        await pilot.press("home")
        await wait_until(pilot, lambda: len(messages.messages) == 80)
        assert client.messages.history_calls[-1] == (GENERAL, make_message(31).id)
        assert not messages.has_more_before


# (e) ------------------------------------------------------------------------------------------


async def test_vim_navigation_and_modes() -> None:
    client = FakeClient()
    async with running(client, preset="vim") as (app, pilot):
        chat = await open_general(app, pilot)
        messages = chat.query_one(MessageList)
        status = chat.query_one(StatusBar)
        messages.focus()
        await settle(pilot)
        assert status.mode == "NORMAL"
        start = messages.highlighted
        assert start is not None
        await pilot.press("k", "k")
        assert messages.highlighted == start - 2
        await pilot.press("g")
        assert status.chord == "g"
        await pilot.press("g")
        assert messages.highlighted_message() == messages.messages[0]
        await pilot.press("i")
        composer = chat.query_one(Composer)
        assert app.focused is composer
        assert status.mode == "INSERT"
        await pilot.press("j", "k")
        assert composer.text == "jk"
        assert messages.highlighted_message() == messages.messages[0]
        await pilot.press("escape")
        assert app.focused is messages
        assert status.mode == "NORMAL"
        await pilot.press("x")  # unbound in NORMAL: ignored
        assert composer.text == "jk"


async def test_helix_bottom_and_space_switcher() -> None:
    client = FakeClient()
    async with running(client, preset="helix") as (app, pilot):
        chat = await open_general(app, pilot)
        messages = chat.query_one(MessageList)
        messages.focus()
        await pilot.press("g", "g")
        assert messages.highlighted_message() == messages.messages[0]
        await pilot.press("g", "e")
        assert messages.highlighted == messages.option_count - 1
        await pilot.press("space", "f")
        await settle(pilot)
        assert isinstance(app.screen, CommandPalette)


async def test_standard_typing_in_message_list_goes_to_composer() -> None:
    client = FakeClient()
    async with running(client) as (app, pilot):
        chat = await open_general(app, pilot)
        chat.query_one(MessageList).focus()
        await pilot.press("h")
        composer = chat.query_one(Composer)
        assert app.focused is composer
        assert composer.text == "h"


async def test_modifier_order_reaches_next_unread() -> None:
    client = FakeClient()
    async with running(client) as (app, pilot):
        chat = await open_general(app, pilot)
        app.store.channels[RANDOM] = replace(
            app.store.channels[RANDOM], last_message_id=make_message(99, RANDOM).id
        )
        chat.query_one(MessageList).focus()
        await pilot.press("alt+shift+down")
        await settle(pilot)
        assert chat.active_channel == RANDOM


# (f) ------------------------------------------------------------------------------------------


async def test_send_pending_resolves_and_failed_retry_reuses_nonce() -> None:
    client = FakeClient()
    async with running(client) as (app, pilot):
        chat = await open_general(app, pilot)
        messages = chat.query_one(MessageList)
        chat.query_one(Composer).focus()
        await pilot.press(*"hello", "enter")
        await wait_until(pilot, lambda: len(client.messages.sent) == 1)
        await settle(pilot)
        last = messages.messages[-1]
        assert last.content == "hello"
        assert not last.pending
        assert [m.content for m in messages.messages].count("hello") == 1

        client.messages.fail_sends = 1
        await pilot.press(*"oops", "enter")
        await wait_until(pilot, lambda: messages.messages[-1].failed)
        failed = messages.messages[-1]
        messages.focus()
        messages.highlighted = messages.option_count - 1
        await pilot.press("R")
        await wait_until(pilot, lambda: len(client.messages.sent) == 3)
        await settle(pilot)
        assert client.messages.sent[1][3] == client.messages.sent[2][3] == failed.nonce
        assert not messages.messages[-1].failed
        assert [m.content for m in messages.messages].count("oops") == 1


async def test_gateway_echo_before_rest_response_does_not_duplicate() -> None:
    client = FakeClient()
    async with running(client) as (app, pilot):
        chat = await open_general(app, pilot)
        messages = chat.query_one(MessageList)
        chat.query_one(Composer).focus()
        await pilot.press(*"echo")
        nonce_holder: list[str] = []
        original_send = client.messages.send

        async def send_with_echo(channel_id, content, *, reply_to=None, nonce=None):
            sent = await original_send(channel_id, content, reply_to=reply_to, nonce=nonce)
            nonce_holder.append(nonce or "")
            client.gateway.push(MessageCreated(sent))
            await pilot.pause()
            return sent

        client.messages.send = send_with_echo  # type: ignore[method-assign]
        await pilot.press("enter")
        await wait_until(pilot, lambda: bool(nonce_holder))
        await settle(pilot)
        assert [m.content for m in messages.messages].count("echo") == 1


# (g) ------------------------------------------------------------------------------------------


async def test_reply_edit_and_delete_own_message() -> None:
    client = FakeClient()
    async with running(client) as (app, pilot):
        chat = await open_general(app, pilot)
        messages = chat.query_one(MessageList)
        composer = chat.query_one(Composer)
        messages.focus()
        # Highlight a message from Friend and reply to it.
        friend_index = messages.option_count - 2
        messages.highlighted = friend_index
        target = messages.highlighted_message()
        assert target is not None
        assert target.author == FRIEND
        await pilot.press("r")
        assert "Replying to Friend" in text_of(chat.query_one("#banner", Static))
        await pilot.press(*"sure", "enter")
        await wait_until(pilot, lambda: len(client.messages.sent) == 1)
        reference = client.messages.sent[0][2]
        assert reference is not None
        assert reference.message_id == target.id

        # Editing someone else's message is refused.
        messages.focus()
        messages.highlighted = friend_index
        await pilot.press("e")
        assert composer.text == ""

        # Edit our own message (message 30 is from Me).
        own = next(
            m for m in reversed(messages.messages) if m.author == ME and m.id == make_message(30).id
        )
        messages.highlighted = messages.get_option_index("m" + own.id)
        await pilot.press("e")
        assert app.focused is composer
        assert composer.text == own.content
        composer.load_text("edited text")
        await pilot.press("enter")
        await wait_until(pilot, lambda: bool(client.messages.edits))
        await settle(pilot)
        assert client.messages.edits == [(GENERAL, own.id, "edited text")]
        assert next(m for m in messages.messages if m.id == own.id).content == "edited text"

        # Delete it after confirmation.
        messages.focus()
        messages.highlighted = messages.get_option_index("m" + own.id)
        await pilot.press("delete")
        await wait_until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await wait_until(pilot, lambda: bool(client.messages.deletes))
        await settle(pilot)
        assert own.id not in {m.id for m in messages.messages}


async def test_up_in_empty_composer_edits_last_own_message() -> None:
    client = FakeClient()
    async with running(client) as (app, pilot):
        chat = await open_general(app, pilot)
        composer = chat.query_one(Composer)
        composer.focus()
        await pilot.press("up")
        assert chat.editing is not None
        assert chat.editing.author == ME
        await pilot.press("escape")
        assert chat.editing is None
        assert composer.text == ""


# (h) ------------------------------------------------------------------------------------------


async def test_unread_mentions_and_ack_on_open() -> None:
    client = FakeClient()
    async with running(client) as (app, pilot):
        chat = await open_general(app, pilot)
        incoming = make_message(50, RANDOM, OTHER, content=f"hey <@{ME.id}>", mention_ids=(ME.id,))
        client.messages.histories[RANDOM].append(incoming)  # what Discord returns on open
        client.gateway.push(MessageCreated(incoming))
        await wait_until(pilot, lambda: app.store.is_unread(RANDOM))
        assert app.store.mention_count(RANDOM) == 1
        tree = chat.query_one(ChannelTree)
        label = str(tree._channel_nodes[RANDOM].label)
        assert "(1)" in label
        rail = chat.query_one(ServerRail)
        assert " 1" in str(rail.get_option_at_index(1).prompt)
        chat.open_channel(RANDOM)
        await wait_until(pilot, lambda: (RANDOM, incoming.id) in client.messages.acks, rounds=120)
        assert not app.store.is_unread(RANDOM)
        assert "(1)" not in str(tree._channel_nodes[RANDOM].label)


# (i) ------------------------------------------------------------------------------------------


async def test_typing_indicator_text_and_expiry() -> None:
    client = FakeClient()
    async with running(client) as (app, pilot):
        chat = await open_general(app, pilot)
        typing = chat.query_one("#typing", Static)
        clock = [100.0]
        app.store._clock = lambda: clock[0]
        client.gateway.push(TypingStarted(GENERAL, GUILD_ID, FRIEND.id, 0))
        await wait_until(pilot, lambda: text_of(typing) == "Friend is typing…")
        client.gateway.push(TypingStarted(GENERAL, GUILD_ID, OTHER.id, 0))
        await wait_until(pilot, lambda: text_of(typing) == "Friend and Other are typing…")
        app.store.users["9"] = replace(OTHER, id="9", username="third", global_name=None)
        client.gateway.push(TypingStarted(GENERAL, GUILD_ID, "9", 0))
        await wait_until(pilot, lambda: text_of(typing) == "Several people are typing…")
        clock[0] = 200.0
        await wait_until(pilot, lambda: text_of(typing) == "", rounds=150)


# (j) ------------------------------------------------------------------------------------------


async def test_captcha_error_is_explained() -> None:
    client = FakeClient(stored_session=False)
    client.auth.captcha = True
    async with running(client) as (app, pilot):
        await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen))
        login = app.screen
        await settle(pilot)
        await pilot.click("#login")
        await pilot.press(*"me@example.com")
        await pilot.click("#password")
        await pilot.press(*"pw", "enter")
        await wait_until(
            pilot, lambda: any("CAPTCHA" in str(n.message) for n in app._notifications)
        )
        assert isinstance(login, LoginScreen)


# (k) ------------------------------------------------------------------------------------------


async def test_logout_and_second_login() -> None:
    client = FakeClient()
    async with running(client) as (app, pilot):
        await in_chat(app, pilot)
        app.action_logout()
        await wait_until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen))
        assert app.store.me is None
        login = app.screen
        await wait_until(
            pilot,
            lambda: login.query_one("#login-switcher", ContentSwitcher).current == "login-tabs",
        )
        await pilot.click("#login")
        await pilot.press(*"me@example.com")
        await pilot.click("#password")
        await pilot.press(*"pw", "enter")
        chat = await in_chat(app, pilot)
        assert chat.query_one(ServerRail).option_count == 2
        assert client.gateway.connects == 2


# (l) ------------------------------------------------------------------------------------------


async def test_narrow_layout_hides_panes_and_shows_overlay() -> None:
    client = FakeClient()
    async with running(client, size=(60, 24)) as (app, pilot):
        chat = await in_chat(app, pilot)
        assert not chat.query_one("#rail").display or chat.query_one("#rail").region.width == 0
        assert chat.query_one("#sidebar").region.width == 0
        chat.query_one(MessageList).focus()
        await pilot.press("alt+c")
        await settle(pilot)
        assert chat.query_one("#sidebar").region.width > 0


# (m) ------------------------------------------------------------------------------------------


async def test_settings_change_theme_and_preset_live_and_persist(tmp_path: Path) -> None:
    client = FakeClient()
    async with running(client, tmp_path=tmp_path) as (app, pilot):
        chat = await in_chat(app, pilot)
        await pilot.press("f2")
        await wait_until(pilot, lambda: isinstance(app.screen, SettingsScreen))
        screen = app.screen
        screen.query_one("#theme", Select).value = "tcord-light"
        screen.query_one("#preset", Select).value = "vim"
        await pilot.press("ctrl+s")
        await wait_until(pilot, lambda: app.theme == "tcord-light")
        assert app.keymap.preset == "vim"
        await settle(pilot)
        chat.query_one(MessageList).focus()
        await settle(pilot)
        assert chat.query_one(StatusBar).mode == "NORMAL"
    saved, warnings = SettingsFile(tmp_path / "settings.toml").load()
    assert warnings == []
    assert saved.appearance.theme == "tcord-light"
    assert saved.navigation.preset == "vim"


@pytest.mark.parametrize("preset", ["standard", "vim", "helix"])
async def test_help_screen_lists_effective_bindings(preset: str) -> None:
    client = FakeClient()
    async with running(client, preset=preset) as (app, pilot):
        await in_chat(app, pilot)
        await pilot.press("f1")
        await settle(pilot)
        body = str(app.screen.query_one("#help-body Static", Static).render())
        assert "Quick switcher" in body
        assert app.keymap.display("app.switcher") in body
        await pilot.press("escape")
        await settle(pilot)
        assert isinstance(app.screen, ChatScreen)


async def test_switcher_opens_dm() -> None:
    client = FakeClient()
    async with running(client) as (app, pilot):
        chat = await in_chat(app, pilot)
        await pilot.press("ctrl+k")
        await wait_until(pilot, lambda: isinstance(app.screen, CommandPalette))
        await pilot.press(*"@fri")
        await settle(pilot, 10)
        await pilot.press("enter")
        await wait_until(pilot, lambda: chat.active_channel == DM)
