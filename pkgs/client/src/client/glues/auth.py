import tcord_core

from ..models import AuthState, MfaMethod
from ..protocols import AuthProtocol
from . import convert


class Auth(AuthProtocol):
    """Authentication facade; secrets never leave the native engine."""

    def __init__(self, engine: tcord_core.Session) -> None:
        self._engine = engine

    def state(self) -> AuthState:
        return convert.auth_state(self._engine.auth_state())

    async def wait_for_change(self, after_version: int) -> AuthState:
        return convert.auth_state(await self._engine.wait_auth_change(after_version))

    async def resume_session(self) -> AuthState:
        return convert.auth_state(await self._engine.resume_session())

    async def login_password(self, login: str, password: str) -> AuthState:
        return convert.auth_state(await self._engine.login_password(login, password))

    async def submit_mfa(self, method: MfaMethod, code: str) -> AuthState:
        return convert.auth_state(await self._engine.submit_mfa(method.value, code))

    async def send_sms_code(self) -> str | None:
        return await self._engine.send_mfa_sms()

    async def start_qr_login(self) -> AuthState:
        return convert.auth_state(await self._engine.start_qr_login())

    def cancel_qr_login(self) -> None:
        self._engine.cancel_qr_login()

    async def logout(self) -> AuthState:
        return convert.auth_state(await self._engine.logout())

    async def set_remember_session(self, enabled: bool) -> AuthState:
        return convert.auth_state(await self._engine.set_remember_session(enabled))
