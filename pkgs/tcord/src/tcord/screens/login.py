from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from client.models import AuthFailure, AuthKind, AuthState, MfaMethod
from textual import on
from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    Input,
    LoadingIndicator,
    RadioButton,
    RadioSet,
    Static,
    TabbedContent,
    TabPane,
)

from ..errors import captcha_text
from ..i18n import _
from ..qr import qr_text

if TYPE_CHECKING:
    from ..app import TcordApp


def failure_text(failure: AuthFailure, retry_after: float | None = None) -> str:
    match failure:
        case AuthFailure.INVALID_CREDENTIALS:
            return _("Login or password is invalid.")
        case AuthFailure.INVALID_CODE:
            return _("Invalid code. Try again.")
        case AuthFailure.CAPTCHA_REQUIRED:
            return captcha_text()
        case AuthFailure.RATE_LIMITED:
            return _("Too many attempts — try again in {seconds}s.").format(
                seconds=max(1, round(retry_after or 0))
            )
        case AuthFailure.NETWORK:
            return _("Couldn't reach Discord.")
        case AuthFailure.QR_CANCELLED:
            return _("QR login was cancelled.")
        case AuthFailure.QR_EXPIRED:
            return _("The QR code expired.")
        case AuthFailure.PHONE_VERIFICATION_REQUIRED:
            return _("Discord requires phone verification. Finish it in the official app first.")
        case AuthFailure.ACCOUNT_DISABLED:
            return _("This account is disabled or scheduled for deletion.")
        case AuthFailure.WEBAUTHN_UNSUPPORTED:
            return _("Security keys aren't supported. Use another two-factor method.")
        case AuthFailure.SESSION_EXPIRED:
            return _("Your session expired. Please log in again.")
        case AuthFailure.CREDENTIAL_STORE:
            return _("The system keyring is unavailable.")
    return str(failure)


def _method_label(method: MfaMethod) -> str:
    match method:
        case MfaMethod.TOTP:
            return _("Authenticator app")
        case MfaMethod.SMS:
            return _("SMS")
        case MfaMethod.BACKUP:
            return _("Backup code")
    return str(method)


class LoginScreen(Screen):
    """Email/password with MFA, QR login, and the session-restore state."""

    def __init__(self) -> None:
        super().__init__()
        self.state: AuthState | None = None
        self.restoring = True
        self._countdown: Timer | None = None
        self._mfa_methods: tuple[MfaMethod, ...] = ()
        self._composed = False

    @property
    def tcord(self) -> TcordApp:
        return cast("TcordApp", self.app)

    def compose(self) -> ComposeResult:
        with Center(), Vertical(id="login-card"):
            yield Static(_("Welcome back!"), id="login-title")
            yield Static(_("We're so excited to see you again!"), id="login-subtitle")
            with ContentSwitcher(initial="restore-view", id="login-switcher"):
                with Vertical(id="restore-view"):
                    yield LoadingIndicator(id="restore-spinner")
                    yield Static(_("Restoring your session…"), id="restore-status")
                    yield Button(_("Retry"), id="restore-retry", classes="hidden")
                with TabbedContent(id="login-tabs"):
                    with TabPane(_("Email"), id="tab-email"):  # noqa: SIM117
                        with ContentSwitcher(initial="credentials", id="email-switcher"):
                            with Vertical(id="credentials"):
                                yield Input(placeholder=_("Email or phone number"), id="login")
                                yield Input(placeholder=_("Password"), password=True, id="password")
                                yield Checkbox(
                                    _("Remember me on this device"),
                                    self.tcord.settings.general.remember_session,
                                    id="remember",
                                )
                                yield Button(_("Log In"), id="submit", variant="primary")
                            with Vertical(id="mfa"):
                                yield Static(_("Two-factor authentication"), classes="heading")
                                yield RadioSet(id="mfa-methods")
                                yield Input(placeholder=_("Code"), password=True, id="code")
                                yield Button(_("Send SMS code"), id="send-sms", classes="hidden")
                                yield Static(id="sms-status")
                                yield Button(_("Verify"), id="verify", variant="primary")
                                yield Button(_("Back"), id="back")
                    with TabPane(_("QR Code"), id="tab-qr"):
                        yield Static(id="qr-status")
                        yield Static(id="qr-code")
                        yield Static(id="qr-countdown")
                        yield Button(_("Try again"), id="qr-retry", classes="hidden")
            yield Static(id="login-error")

    def on_mount(self) -> None:
        self._composed = True
        if self.state is not None:
            self.update_state(self.state)

    # ----- state -----------------------------------------------------------------------------

    def update_state(self, state: AuthState) -> None:
        self.state = state
        if not self._composed:
            return
        switcher = self.query_one("#login-switcher", ContentSwitcher)
        error = self.query_one("#login-error", Static)
        if state.kind == AuthKind.RESTORING:
            self.restoring = True
            switcher.current = "restore-view"
            error.update("")
            return
        if self.restoring and state.failure == AuthFailure.NETWORK:
            switcher.current = "restore-view"
            self.query_one("#restore-spinner").display = False
            self.query_one("#restore-status", Static).update(_("Couldn't reach Discord."))
            self.query_one("#restore-retry").remove_class("hidden")
            return
        if switcher.current == "restore-view":
            self.restoring = False
            switcher.current = "login-tabs"
            self.query_one("#login", Input).focus()
        error.update(
            failure_text(state.failure, state.retry_after) if state.failure is not None else ""
        )
        if state.kind == AuthKind.WAITING_FOR_MFA:
            self._show_mfa(state.mfa_methods)
        elif state.kind in (AuthKind.WAITING_FOR_CREDENTIALS, AuthKind.LOGGED_OUT):
            self.query_one("#email-switcher", ContentSwitcher).current = "credentials"
        self._update_qr(state)

    def _show_mfa(self, methods: tuple[MfaMethod, ...]) -> None:
        self.query_one("#email-switcher", ContentSwitcher).current = "mfa"
        if methods != self._mfa_methods:
            self._mfa_methods = methods
            radio = self.query_one("#mfa-methods", RadioSet)
            radio.remove_children()
            for index, method in enumerate(methods or (MfaMethod.TOTP,)):
                radio.mount(
                    RadioButton(_method_label(method), value=index == 0, id=f"mfa-{method.value}")
                )
            self._sms_visibility(methods[0] if methods else MfaMethod.TOTP)
        self.query_one("#code", Input).focus()

    def _selected_method(self) -> MfaMethod:
        pressed = self.query_one("#mfa-methods", RadioSet).pressed_button
        if pressed is not None and pressed.id:
            return MfaMethod(pressed.id.removeprefix("mfa-"))
        return self._mfa_methods[0] if self._mfa_methods else MfaMethod.TOTP

    def _sms_visibility(self, method: MfaMethod) -> None:
        self.query_one("#send-sms").set_class(method != MfaMethod.SMS, "hidden")

    def _update_qr(self, state: AuthState) -> None:
        status = self.query_one("#qr-status", Static)
        code = self.query_one("#qr-code", Static)
        retry = self.query_one("#qr-retry")
        if state.kind == AuthKind.WAITING_FOR_QR:
            retry.add_class("hidden")
            if state.qr_user is not None:
                status.update(
                    _("Check your phone — logging in as {username}").format(
                        username=state.qr_user.username
                    )
                )
                code.update("")
            elif state.qr_url:
                status.update(_("Scan with the Discord mobile app"))
                code.update(
                    qr_text(state.qr_url, ascii_only=self.tcord.settings.appearance.ascii_only)
                )
                self._start_countdown()
            else:
                status.update(_("Generating QR code…"))
                code.update("")
        elif state.failure in (AuthFailure.QR_CANCELLED, AuthFailure.QR_EXPIRED):
            status.update(failure_text(state.failure))
            code.update("")
            retry.remove_class("hidden")
            self._stop_countdown()

    def _start_countdown(self) -> None:
        if self._countdown is None:
            self._countdown = self.set_interval(1.0, self._tick)
        self._tick()

    def _stop_countdown(self, *, clear: bool = True) -> None:
        if self._countdown is not None:
            self._countdown.stop()
            self._countdown = None
        if clear:
            self.query_one("#qr-countdown", Static).update("")

    def _tick(self) -> None:
        expires = self.state.qr_expires_at if self.state else None
        if expires is None:
            return
        seconds = max(0, int((expires - datetime.now(UTC)).total_seconds()))
        self.query_one("#qr-countdown", Static).update(
            _("Expires in {minutes}:{seconds:02d}").format(
                minutes=seconds // 60, seconds=seconds % 60
            )
        )

    # ----- actions ---------------------------------------------------------------------------

    @on(Input.Submitted, "#password")
    @on(Button.Pressed, "#submit")
    def submit_credentials(self) -> None:
        login = self.query_one("#login", Input).value.strip()
        password = self.query_one("#password", Input).value
        if not login or not password:
            self.query_one("#login-error", Static).update(
                _("Enter your email or phone number and password.")
            )
            return
        self.query_one("#login-error", Static).update("")
        self.query_one("#password", Input).value = ""
        self.tcord.run_auth(self.tcord.client.auth.login_password(login, password))

    @on(Input.Submitted, "#code")
    @on(Button.Pressed, "#verify")
    def submit_code(self) -> None:
        code = self.query_one("#code", Input).value.strip()
        if not code:
            self.query_one("#login-error", Static).update(_("Enter the code."))
            return
        self.query_one("#code", Input).value = ""
        self.tcord.run_auth(self.tcord.client.auth.submit_mfa(self._selected_method(), code))

    @on(Button.Pressed, "#send-sms")
    def send_sms(self) -> None:
        async def run() -> None:
            phone = await self.tcord.guard(self.tcord.client.auth.send_sms_code())
            if phone:
                self.query_one("#sms-status", Static).update(
                    _("Code sent to {phone}").format(phone=phone)
                )

        self.run_worker(run(), group="sms", exclusive=True)

    @on(Button.Pressed, "#back")
    def back_to_credentials(self) -> None:
        self.tcord.run_auth(self.tcord.client.auth.logout())

    @on(Button.Pressed, "#restore-retry")
    def retry_restore(self) -> None:
        self.query_one("#restore-retry").add_class("hidden")
        self.query_one("#restore-spinner").display = True
        self.query_one("#restore-status", Static).update(_("Restoring your session…"))
        self.tcord.run_auth(self.tcord.client.auth.resume_session())

    @on(Button.Pressed, "#qr-retry")
    def retry_qr(self) -> None:
        self.tcord.run_auth(self.tcord.client.auth.start_qr_login())

    @on(RadioSet.Changed, "#mfa-methods")
    def method_changed(self) -> None:
        self._sms_visibility(self._selected_method())

    @on(Checkbox.Changed, "#remember")
    def remember_changed(self, event: Checkbox.Changed) -> None:
        self.tcord.set_remember_session(event.value)

    @on(TabbedContent.TabActivated, "#login-tabs")
    def tab_changed(self, event: TabbedContent.TabActivated) -> None:
        # Activation messages can arrive late (e.g. the initial tab); trust the live state.
        active = self.query_one("#login-tabs", TabbedContent).active
        if event.pane.id != active:
            return
        waiting = self.state is not None and self.state.kind == AuthKind.WAITING_FOR_QR
        if active == "tab-qr":
            if not waiting:
                self.query_one("#qr-status", Static).update(_("Generating QR code…"))
                self.tcord.run_auth(self.tcord.client.auth.start_qr_login())
        else:
            self._stop_countdown()
            if waiting:
                self.tcord.client.auth.cancel_qr_login()

    def on_unmount(self) -> None:
        self._stop_countdown(clear=False)
