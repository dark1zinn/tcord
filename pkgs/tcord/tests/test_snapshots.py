"""Visual regression snapshots. Update deliberately with ``pytest --snapshot-update``."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, tzinfo
from typing import Self

import pytest
from conftest import settle, wait_until
from fakes import GENERAL, GUILD_ID, FakeClient
from tcord import formatting
from tcord.app import TcordApp
from tcord.screens.chat import ChatScreen
from tcord.screens.login import LoginScreen
from tcord.settings import AppearanceSettings, Settings
from tcord.widgets.composer import Composer
from tcord.widgets.messages import MessageList
from textual.pilot import Pilot
from textual.widgets import ContentSwitcher, Input

type SnapCompare = Callable[..., bool]


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        frozen = cls(2026, 1, 1, 18, 0, tzinfo=UTC)
        return frozen if tz is None else frozen.astimezone(tz)


@pytest.fixture(autouse=True)
def _deterministic_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    monkeypatch.setattr(formatting, "datetime", FrozenDatetime)
    yield
    monkeypatch.undo()
    time.tzset()


def _no_blink(app: TcordApp) -> None:
    for widget in app.screen.query(Input):
        widget.cursor_blink = False
    for widget in app.screen.query(Composer):
        widget.cursor_blink = False


def test_login_snapshot(snap_compare: SnapCompare) -> None:
    app = TcordApp(FakeClient(stored_session=False))

    async def ready(pilot: Pilot) -> None:
        await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen))
        await wait_until(
            pilot,
            lambda: (
                app.screen.query_one("#login-switcher", ContentSwitcher).current == "login-tabs"
            ),
        )
        _no_blink(app)
        await settle(pilot)

    assert snap_compare(app, terminal_size=(120, 36), run_before=ready)


def _chat_app(density: str) -> TcordApp:
    settings = Settings(appearance=AppearanceSettings(message_density=density))
    return TcordApp(FakeClient(), settings)


def _open_general(app: TcordApp) -> Callable[[Pilot], object]:
    async def run(pilot: Pilot) -> None:
        await wait_until(pilot, lambda: isinstance(app.screen, ChatScreen))
        chat = app.screen
        assert isinstance(chat, ChatScreen)
        chat.choose_guild(GUILD_ID)
        chat.open_channel(GENERAL)
        messages = chat.query_one(MessageList)
        await wait_until(pilot, lambda: len(messages.messages) == 30)
        _no_blink(app)
        await settle(pilot)

    return run


def test_chat_cozy_snapshot(snap_compare: SnapCompare) -> None:
    app = _chat_app("cozy")
    assert snap_compare(app, terminal_size=(120, 36), run_before=_open_general(app))


def test_chat_compact_narrow_snapshot(snap_compare: SnapCompare) -> None:
    app = _chat_app("compact")
    assert snap_compare(app, terminal_size=(80, 24), run_before=_open_general(app))
