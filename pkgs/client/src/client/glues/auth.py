import asyncio
from typing import Any

from ..protocols import AuthProtocol


class Auth(AuthProtocol):
    """Async facade over the Rust session; secrets never enter Python state."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def begin_credentials(self) -> Any:
        return await asyncio.to_thread(self._engine.begin_credentials)

    async def login_with_password(self, email: str, password: str) -> Any:
        return await asyncio.to_thread(self._engine.login_with_password, email, password)

    async def submit_mfa(self, code: str) -> Any:
        return await asyncio.to_thread(self._engine.submit_mfa, code)

    async def login_with_qr(self) -> Any:
        return await asyncio.to_thread(self._engine.begin_qr_login)

    async def resume_stored_session(self) -> Any:
        return await asyncio.to_thread(self._engine.resume_stored_session)

    async def auth_state(self) -> Any:
        return await asyncio.to_thread(self._engine.auth_state)

    async def logoff(self) -> Any:
        return await asyncio.to_thread(self._engine.logout)
