from tui import Tui


class State:
    kind = "authenticated"
    qr_payload = None
    expires_at = None
    message = None


class Auth:
    async def resume_stored_session(self):
        return State()

    async def auth_state(self):
        return State()

    async def login_with_password(self, *_):
        return State()

    async def submit_mfa(self, *_):
        return State()

    async def login_with_qr(self):
        return State()

    async def logoff(self):
        return State()


class Relations:
    async def get_guilds(self):
        return []

    async def get_dms(self):
        return []

    async def get_channels(self, _):
        return []


class Channel:
    async def load_message_history(self, _):
        return []

    async def send_message(self, *_):
        raise AssertionError("no active channel")


class Client:
    auth = Auth()
    relations = Relations()
    channel = Channel()


async def test_captcha_gate_and_logout_reset() -> None:
    app = Tui(Client())
    async with app.run_test() as pilot:
        assert not app.query_one("#chat").has_class("hidden")
        app.action_new_dm()
        assert "CAPTCHA" in str(app.query_one("#login-status").render())
        await app.action_logout()
        assert app.query_one("#chat").has_class("hidden")
        await pilot.press("q")
