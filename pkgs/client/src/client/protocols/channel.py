from collections.abc import Awaitable, Callable
from typing import Protocol

from ..models import MessageInfo


class ChannelProtocol(Protocol):
    async def send_message(self, channel_id: str, content: str) -> MessageInfo: ...

    async def load_message_history(
        self,
        channel_id: str,
        before: str | None = None,
        limit: int = 50,
    ) -> list[MessageInfo]: ...

    def on_message_received(self, callback: Callable[[MessageInfo], Awaitable[None]]) -> None: ...
