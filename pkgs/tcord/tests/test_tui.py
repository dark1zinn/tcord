from pathlib import Path

from client.models import (
    AuthKind,
    AuthState,
    Channel,
    GatewayEvent,
    Guild,
    MessageInfo,
    MfaMethod,
    ReadySnapshot,
    User,
)
from tcord.app import TcordApp
from tcord.screens.settings import SettingsScreen
from tcord.settings import Settings, SettingsFile
from textual.widgets import Select

AUTHENTICATED = AuthState(version=1, kind=AuthKind.AUTHENTICATED)


class Auth:
    def state(self) -> AuthState:
        return AUTHENTICATED

    async def wait_for_change(self, after_version: int) -> AuthState:
        return AUTHENTICATED

    async def resume_session(self) -> AuthState:
        return AUTHENTICATED

    async def login_password(self, login: str, password: str) -> AuthState:
        return AUTHENTICATED

    async def submit_mfa(self, method: MfaMethod, code: str) -> AuthState:
        return AUTHENTICATED

    async def send_sms_code(self) -> str | None:
        return None

    async def start_qr_login(self) -> AuthState:
        return AUTHENTICATED

    def cancel_qr_login(self) -> None:
        pass

    async def logout(self) -> AuthState:
        return AuthState(version=2, kind=AuthKind.LOGGED_OUT)

    async def set_remember_session(self, enabled: bool) -> AuthState:
        return AUTHENTICATED


class Gateway:
    async def connect(self) -> ReadySnapshot:
        return ReadySnapshot(User("1", "me"), (), (), (), ())

    async def next_event(self) -> GatewayEvent | None:
        return None

    def subscribe_guild(self, guild_id: str) -> None:
        pass

    def disconnect(self) -> None:
        pass

    async def guilds(self) -> list[Guild]:
        return []

    async def guild_channels(self, guild_id: str) -> list[Channel]:
        return []

    async def private_channels(self) -> list[Channel]:
        return []


class Messages:
    async def history(
        self, channel_id: str, before: str | None = None, limit: int = 50
    ) -> list[MessageInfo]:
        return []

    async def send(self, channel_id: str, content: str, **_: object) -> MessageInfo:
        raise AssertionError("no active channel")

    async def edit(self, channel_id: str, message_id: str, content: str) -> MessageInfo:
        raise AssertionError("not used")

    async def delete(self, channel_id: str, message_id: str) -> None:
        pass

    async def trigger_typing(self, channel_id: str) -> None:
        pass

    async def ack(self, channel_id: str, message_id: str) -> None:
        pass


class Client:
    auth = Auth()
    gateway = Gateway()
    messages = Messages()

    def close(self) -> None:
        pass


async def test_captcha_gate_and_logout_reset() -> None:
    app = TcordApp(Client())
    async with app.run_test() as pilot:
        assert not app.query_one("#chat").has_class("hidden")
        app.action_new_dm()
        assert "CAPTCHA" in str(app.query_one("#login-status").render())
        await app.action_logout()
        assert app.query_one("#chat").has_class("hidden")
        await pilot.press("q")


async def test_settings_screen_applies_and_saves_theme(tmp_path: Path) -> None:
    settings_file = SettingsFile(tmp_path / "settings.toml")
    app = TcordApp(Client(), Settings(), settings_file)
    async with app.run_test() as pilot:
        await pilot.press("f2")
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        screen.query_one("#theme", Select).value = "textual-light"
        await pilot.press("ctrl+s")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert app.theme == "textual-light"
    saved, warnings = settings_file.load()
    assert saved.appearance.theme == "textual-light"
    assert warnings == []
