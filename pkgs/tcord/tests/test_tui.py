from client.models import AuthKind, AuthState, Channel, Guild, MessageInfo, MfaMethod
from tui import Tui

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


class Client:
    auth = Auth()
    gateway = Gateway()
    messages = Messages()

    def close(self) -> None:
        pass


async def test_captcha_gate_and_logout_reset() -> None:
    app = Tui(Client())
    async with app.run_test() as pilot:
        assert not app.query_one("#chat").has_class("hidden")
        app.action_new_dm()
        assert "CAPTCHA" in str(app.query_one("#login-status").render())
        await app.action_logout()
        assert app.query_one("#chat").has_class("hidden")
        await pilot.press("q")
