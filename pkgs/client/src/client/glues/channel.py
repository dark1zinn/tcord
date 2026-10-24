import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from ..models import MessageInfo
from ..protocols import ChannelProtocol


class Channel(ChannelProtocol):
    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._message_callbacks: list[Callable[[MessageInfo], Awaitable[None]]] = []

    async def send_message(self, channel_id: str, content: str) -> MessageInfo:
        return MessageInfo.from_wire(await self._engine.send_message(channel_id, content))

    async def load_message_history(
        self, channel_id: str, before: str | None = None, limit: int = 50
    ) -> list[MessageInfo]:
        messages = await self._engine.load_message_history(channel_id, before, limit)
        return [MessageInfo.from_wire(message) for message in messages]

    def on_message_received(
        self, callback: Callable[[MessageInfo], Awaitable[None]]
    ) -> None:
        self._message_callbacks.append(callback)

    async def dispatch_message(self, raw_message: dict[str, Any]) -> None:
        message = MessageInfo.from_wire(raw_message)
        await asyncio.gather(
            *(callback(message) for callback in tuple(self._message_callbacks))
        )
