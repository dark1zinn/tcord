from __future__ import annotations

from collections.abc import Awaitable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from client.errors import TcordError
from client.models import AuthKind, AuthState, Ready
from client.protocols import ClientProtocol
from client.state import ChatStore
from textual.app import App, SystemCommand
from textual.binding import Binding, BindingType
from textual.command import CommandPalette
from textual.screen import Screen

from . import errors
from .i18n import _
from .keymap import GLOBAL_PRIORITY, Keymap
from .screens.chat import ChatScreen
from .screens.confirm import ConfirmScreen
from .screens.help import HelpScreen
from .screens.login import LoginScreen
from .screens.settings import SettingsScreen
from .settings import PRESETS, Settings, SettingsFile, SettingsLockedError
from .switcher import ChannelSwitcherProvider
from .theme import register_themes


class TcordApp(App):
    """Owns the client, the chat store and the auth/gateway workers; switches screens."""

    CSS_PATH = "tcord.tcss"
    TITLE = "Tcord"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit", show=False, priority=True, id="app.quit"),
        Binding("ctrl+k", "open_switcher", show=False, priority=True, id="app.switcher"),
        Binding("f1", "open_help", show=False, priority=True, id="app.help"),
    ]

    def __init__(
        self,
        client: ClientProtocol,
        settings: Settings | None = None,
        settings_file: SettingsFile | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.settings = settings if settings is not None else Settings()
        self.settings_file = settings_file or SettingsFile(Path("settings.toml"))
        self.store = ChatStore()
        self.keymap, keymap_warnings = Keymap.from_settings(
            self.settings.navigation.preset, self.settings.keymap
        )
        self._startup_warnings = [*(warnings or []), *keymap_warnings]
        self.login_screen: LoginScreen | None = None
        self.chat_screen: ChatScreen | None = None
        self._connecting = False
        self._persist_warned = False

    # ----- lifecycle -------------------------------------------------------------------------

    def on_mount(self) -> None:
        register_themes(self)
        self.apply_settings(self.settings)
        for warning in self._startup_warnings:
            self.notify(warning, severity="warning", timeout=8)
        self.login_screen = LoginScreen()
        self.push_screen(self.login_screen)
        self.run_worker(self._watch_auth(), group="auth-watch", exclusive=True)
        self.run_auth(self.client.auth.resume_session())

    def on_unmount(self) -> None:
        self.client.close()

    async def _watch_auth(self) -> None:
        state = self.client.auth.state()
        self._on_auth(state)
        while True:
            try:
                state = await self.client.auth.wait_for_change(state.version)
            except TcordError:
                return
            self._on_auth(state)

    def _on_auth(self, state: AuthState) -> None:
        if state.kind == AuthKind.AUTHENTICATED:
            remember = self.settings.general.remember_session
            if remember and not state.session_persisted and not self._persist_warned:
                self._persist_warned = True
                self.notify(
                    _(
                        "Couldn't save your session to the system keyring; "
                        "you'll need to log in again next time."
                    ),
                    severity="warning",
                    timeout=8,
                )
            if self.chat_screen is None and not self._connecting:
                self._connecting = True
                self.run_worker(self._connect(), group="connect", exclusive=True)
            return
        if self.chat_screen is not None or self._connecting:
            self._leave_chat()
        if self.login_screen is not None:
            self.login_screen.update_state(state)

    async def _connect(self) -> None:
        try:
            snapshot = await self.client.gateway.connect()
        except TcordError as error:
            self._connecting = False
            self.report(error)
            return
        self.store.load_ready(snapshot)
        chat = ChatScreen()
        self.chat_screen = chat
        self.login_screen = None
        self._connecting = False
        await self._replace_top(chat)
        self.run_worker(self._pump_events(), group="events", exclusive=True)

    async def _pump_events(self) -> None:
        while (event := await self.client.gateway.next_event()) is not None:
            changes = self.store.apply(event)
            chat = self.chat_screen
            if chat is None:
                continue
            if isinstance(event, Ready):
                chat.reload()
            else:
                chat.apply_changes(changes)

    def _leave_chat(self) -> None:
        self.workers.cancel_group(self, "events")
        self.workers.cancel_group(self, "connect")
        self._connecting = False
        self.store.reset()
        self.chat_screen = None
        login = LoginScreen()
        self.login_screen = login
        self.call_later(self._replace_top, login)

    async def _replace_top(self, screen: Screen) -> None:
        while len(self.screen_stack) > 2:
            self.pop_screen()
        await self.switch_screen(screen)

    # ----- calls and errors ------------------------------------------------------------------

    def report(self, error: BaseException) -> None:
        text = errors.describe(error)
        if text:
            self.notify(text, severity="error")

    async def guard[T](self, awaitable: Awaitable[T], *, quiet: bool = False) -> T | None:
        """Await a client call; errors become notifications and ``None``."""
        try:
            return await awaitable
        except TcordError as error:
            if not quiet:
                self.report(error)
            return None

    def run_auth(self, awaitable: Awaitable[object]) -> None:
        """Run an auth call; its resulting state arrives through the auth watcher."""
        self.run_worker(self.guard(awaitable), group="auth")

    def set_remember_session(self, enabled: bool) -> None:
        settings = replace(
            self.settings, general=replace(self.settings.general, remember_session=enabled)
        )

        async def apply() -> None:
            if await self._save_settings(settings):
                self.settings = settings
            await self.guard(self.client.auth.set_remember_session(enabled))

        self.run_worker(apply(), group="settings", exclusive=True)

    # ----- settings --------------------------------------------------------------------------

    def apply_settings(self, settings: Settings) -> None:
        """Apply everything that can change at runtime (language needs a restart)."""
        appearance = settings.appearance
        if appearance.theme in self.available_themes:
            self.theme = appearance.theme
        else:
            self.notify(
                _("Unknown theme {theme}; using the default.").format(theme=appearance.theme),
                severity="warning",
            )
            self.theme = "tcord-dark"
        self.animation_level = "none" if appearance.reduced_motion else "full"
        self.set_class(appearance.ascii_only, "-ascii")
        self.settings = settings
        self.keymap, warnings = Keymap.from_settings(settings.navigation.preset, settings.keymap)
        for warning in warnings:
            self.notify(warning, severity="warning")
        self.set_keymap(self._priority_keymap())
        if self.chat_screen is not None and self.chat_screen.is_mounted:
            self.chat_screen.apply_settings()

    def _priority_keymap(self) -> dict[str, str]:
        """Single-key, non-character bindings of global actions (they must work while typing)."""
        keymap: dict[str, str] = {}
        for action in GLOBAL_PRIORITY:
            keys = [
                sequence[0]
                for sequence in self.keymap.sequences(action)
                if len(sequence) == 1 and len(sequence[0]) > 1
            ]
            keymap[action] = ",".join(keys) if keys else "f24"
        return keymap

    def action_open_settings(self) -> None:
        if isinstance(self.screen, SettingsScreen):
            return
        screen = SettingsScreen(
            self.settings,
            self.settings_file.path,
            sorted(self.available_themes),
            self.keymap.describe(),
        )
        self.push_screen(screen, self._settings_closed)

    def _settings_closed(self, settings: Settings | None) -> None:
        if settings is not None:
            self.run_worker(self._commit_settings(settings), group="settings", exclusive=True)

    async def _commit_settings(self, settings: Settings) -> None:
        previous = self.settings
        if not await self._save_settings(settings):
            return
        self.apply_settings(settings)
        if settings.general.remember_session != previous.general.remember_session:
            await self.guard(
                self.client.auth.set_remember_session(settings.general.remember_session)
            )
        if settings.general.language != previous.general.language:
            self.notify(_("Restart Tcord to apply the language change."))

    async def _save_settings(self, settings: Settings) -> bool:
        try:
            self.settings_file.save(settings)
        except SettingsLockedError:
            question = _("{path} is not valid TOML. Overwrite it with the new settings?").format(
                path=self.settings_file.path
            )
            if not await self.push_screen_wait(ConfirmScreen(question, _("Overwrite"))):
                return False
            self.settings_file.save(settings, force=True)
        except OSError as error:
            self.notify(_("Couldn't save settings: {error}").format(error=error), severity="error")
            return False
        return True

    def _set_preset(self, preset: str) -> None:
        settings = replace(
            self.settings, navigation=replace(self.settings.navigation, preset=preset)
        )
        self.run_worker(self._commit_settings(settings), group="settings", exclusive=True)

    # ----- global actions --------------------------------------------------------------------

    def action_open_switcher(self) -> None:
        if self.chat_screen is None or isinstance(self.screen, CommandPalette):
            return
        self.push_screen(
            CommandPalette(
                providers=[ChannelSwitcherProvider],
                placeholder=_("Where would you like to go?"),
            )
        )

    def action_open_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.screen.dismiss(None)
            return
        self.push_screen(HelpScreen(self.keymap))

    def action_logout(self) -> None:
        async def run() -> None:
            if await self.push_screen_wait(ConfirmScreen(_("Log out of Tcord?"), _("Log Out"))):
                await self.guard(self.client.auth.logout())

        self.run_worker(run(), group="logout", exclusive=True)

    def action_reconnect(self) -> None:
        async def run() -> None:
            self.workers.cancel_group(self, "events")
            self.client.gateway.disconnect()
            snapshot = await self.guard(self.client.gateway.connect())
            if snapshot is None:
                return
            self.store.load_ready(snapshot)
            if self.chat_screen is not None:
                self.chat_screen.reload()
            self.run_worker(self._pump_events(), group="events", exclusive=True)

        self.run_worker(run(), group="connect", exclusive=True)

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        yield SystemCommand(_("Settings"), _("Open Tcord settings"), self.action_open_settings)
        yield SystemCommand(_("Help"), _("Show keyboard shortcuts"), self.action_open_help)
        chat = self.chat_screen
        if chat is None:
            return
        yield SystemCommand(_("Log out"), _("Sign out of this account"), self.action_logout)
        yield SystemCommand(
            _("Toggle server rail"), _("Show or hide the server list"), chat.action_toggle_rail
        )
        yield SystemCommand(
            _("Toggle channel list"),
            _("Show or hide the channel list"),
            chat.action_toggle_channels,
        )
        for preset in PRESETS:
            yield SystemCommand(
                _("Navigation preset: {preset}").format(preset=preset),
                _("Switch key bindings"),
                lambda preset=preset: self._set_preset(preset),
            )
        yield SystemCommand(_("Reconnect"), _("Reconnect to the gateway"), self.action_reconnect)
