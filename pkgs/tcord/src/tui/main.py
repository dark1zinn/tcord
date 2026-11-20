from dataclasses import dataclass, field
from typing import ClassVar

from client import TcordClient
from client.models import AuthKind, MfaMethod
from client.protocols import ClientProtocol
from tcord_core import Session
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static


@dataclass
class ChatState:
    active_channel: str | None = None
    drafts: dict[str, str] = field(default_factory=dict)


class Tui(App):
    BINDINGS: ClassVar = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("tab", "focus_next", "Next"),
        Binding("shift+tab", "focus_previous", "Previous"),
        Binding("question_mark", "toggle_help", "Help"),
        Binding("f1", "toggle_help", "Help"),
        Binding("ctrl+m", "toggle_keymap", "Keymap"),
        Binding("q", "qr_login", "QR login"),
        Binding("ctrl+l", "logout", "Logout"),
        Binding("n", "new_dm", "New DM"),
        Binding("j", "move_down", show=False),
        Binding("k", "move_up", show=False),
        Binding("i", "focus_composer", show=False),
        Binding("escape", "leave_input", show=False),
    ]
    CSS = """
    #login, #chat { height: 1fr; }
    #login { align: center middle; }
    #login-form { width: 50; height: auto; }
    #guilds { width: 24; }
    #channels { width: 28; }
    #timeline { height: 1fr; }
    #composer { dock: bottom; }
    #help { display: none; dock: bottom; }
    .hidden { display: none; }
    """

    def __init__(self, client: ClientProtocol | None = None) -> None:
        super().__init__()
        self.client = client if client is not None else TcordClient(Session())
        self.state = ChatState()
        self.modal_keymap = True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="login"), Vertical(id="login-form"):
            yield Label("Sign in to Tcord")
            yield Input(placeholder="Email", id="email")
            yield Input(placeholder="Password", password=True, id="password")
            yield Input(placeholder="Authenticator or backup code", password=True, id="mfa")
            yield Static("Enter credentials to sign in.", id="login-status")
        with Horizontal(id="chat", classes="hidden"):
            yield ListView(id="guilds")
            yield ListView(id="channels")
            with Vertical():
                yield Static("Select a guild channel or existing DM", id="channel-title")
                yield Static(id="timeline")
                yield Input(placeholder="Message", id="composer")
        yield Static(id="help")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#email", Input).focus()
        await self.client.auth.resume_session()
        await self._await_auth()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "password":
            await self._login()
        elif event.input.id == "mfa":
            await self._mfa()
        elif event.input.id == "composer":
            await self._send()

    async def _login(self) -> None:
        await self.client.auth.login_password(
            self.query_one("#email", Input).value, self.query_one("#password", Input).value
        )
        self.query_one("#password", Input).value = ""
        await self._await_auth()

    async def _mfa(self) -> None:
        methods = self.client.auth.state().mfa_methods or (MfaMethod.TOTP,)
        await self.client.auth.submit_mfa(methods[0], self.query_one("#mfa", Input).value)
        self.query_one("#mfa", Input).value = ""
        await self._await_auth()

    async def _await_auth(self) -> None:
        auth = self.client.auth.state()
        if auth.kind == AuthKind.AUTHENTICATED:
            await self._show_chat()
        elif auth.kind == AuthKind.WAITING_FOR_MFA:
            self.query_one("#mfa", Input).focus()
        elif auth.failure is not None:
            self.query_one("#login-status", Static).update(f"Sign in failed: {auth.failure}")

    async def _show_chat(self) -> None:
        self.query_one("#login").add_class("hidden")
        self.query_one("#chat").remove_class("hidden")
        guilds = await self.client.gateway.guilds()
        dms = await self.client.gateway.private_channels()
        view = self.query_one("#guilds", ListView)
        await view.clear()
        for guild in guilds:
            view.append(ListItem(Label(guild.name), id=f"guild-{guild.id}"))
        for dm in dms:
            names = ", ".join(user.display_name for user in dm.recipients)
            view.append(ListItem(Label(f"DM · {dm.name or names}"), id=f"dm-{dm.id}"))
        view.focus()

    async def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if not event.item or not event.item.id:
            return
        kind, value = event.item.id.split("-", 1)
        if event.list_view.id == "guilds":
            if kind == "dm":
                await self._select_channel(value, str(event.item.query_one(Label).content))
                return
            channels = await self.client.gateway.guild_channels(value)
            view = self.query_one("#channels", ListView)
            await view.clear()
            for channel in channels:
                if channel.is_readable:
                    view.append(ListItem(Label(channel.name or ""), id=f"channel-{channel.id}"))
            view.focus()
        elif event.list_view.id == "channels":
            await self._select_channel(value, str(event.item.query_one(Label).content))

    async def _select_channel(self, channel_id: str, title: str) -> None:
        self.state.active_channel = channel_id
        self.query_one("#channel-title", Static).update(title)
        messages = await self.client.messages.history(channel_id)
        self.query_one("#timeline", Static).update(
            "\n".join(f"{message.author_name}: {message.content}" for message in messages)
        )

    async def _send(self) -> None:
        if not self.state.active_channel:
            return
        composer = self.query_one("#composer", Input)
        if not composer.value:
            return
        message = await self.client.messages.send(self.state.active_channel, composer.value)
        composer.value = ""
        timeline = self.query_one("#timeline", Static)
        timeline.update(f"{timeline.content}\n{message.author_name}: {message.content}")

    def action_focus_composer(self) -> None:
        self.query_one("#composer", Input).focus()

    def action_leave_input(self) -> None:
        self.query_one("#channels", ListView).focus()

    def action_move_down(self) -> None:
        if self.modal_keymap and not isinstance(self.focused, Input):
            self.action_focus_next()

    def action_move_up(self) -> None:
        if self.modal_keymap and not isinstance(self.focused, Input):
            self.action_focus_previous()

    def action_toggle_keymap(self) -> None:
        self.modal_keymap = not self.modal_keymap

    async def action_qr_login(self) -> None:
        auth = await self.client.auth.start_qr_login()
        if auth.kind == AuthKind.WAITING_FOR_QR and auth.qr_url:
            self.query_one("#login-status", Static).update(
                f"Scan QR payload: {auth.qr_url} (expires: {auth.qr_expires_at})"
            )
        else:
            status = f"QR sign-in failed: {auth.failure}" if auth.failure else "Preparing QR."
            self.query_one("#login-status", Static).update(status)

    async def action_logout(self) -> None:
        await self.client.auth.logout()
        self.state = ChatState()
        self.query_one("#chat").add_class("hidden")
        self.query_one("#login").remove_class("hidden")
        self.query_one("#timeline", Static).update("")
        self.query_one("#composer", Input).value = ""
        self.query_one("#email", Input).focus()

    def action_new_dm(self) -> None:
        self.query_one("#login-status", Static).update(
            "Starting new DMs is disabled: Discord may require CAPTCHA completion. "
            "System-webview CAPTCHA support is planned."
        )

    def action_toggle_help(self) -> None:
        self.query_one("#help").toggle_class("hidden")
