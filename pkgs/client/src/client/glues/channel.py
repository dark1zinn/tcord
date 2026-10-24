import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from ..models import MessageInfo
from ..protocols import ChannelProtocol


class Channel(ChannelProtocol):
    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._message_callbacks: list[Callable[[MessageInfo], Awaitable[None]]] = []

    async def send_message(self, channel_id: str, content: str) -> MessageInfo:
        raw = json.loads(
            await asyncio.to_thread(self._engine.send_message, channel_id, content)
        )
        return MessageInfo.from_wire(_message_wire(raw))

    async def load_message_history(
        self, channel_id: str, before: str | None = None, limit: int = 50
    ) -> list[MessageInfo]:
        raw_messages = json.loads(
            await asyncio.to_thread(
                self._engine.load_message_history, channel_id, before, limit
            )
        )
        return [
            MessageInfo.from_wire(_message_wire(message))
            for message in sorted(raw_messages, key=lambda message: message["timestamp"])
        ]

    def on_message_received(
        self, callback: Callable[[MessageInfo], Awaitable[None]]
    ) -> None:
        self._message_callbacks.append(callback)

    async def dispatch_message(self, raw_message: dict[str, Any]) -> None:
        message = MessageInfo.from_wire(_message_wire(raw_message))
        await asyncio.gather(
            *(callback(message) for callback in tuple(self._message_callbacks))
        )


def _message_wire(message: dict[str, Any]) -> dict[str, Any]:
    author = message["author"]
    return {
        "id": message["id"],
        "channel_id": message["channel_id"],
        "author_id": author["id"],
        "author": author["username"],
        "content": message.get("content", ""),
        "timestamp": message["timestamp"],
        "guild_id": message.get("guild_id"),
        "edited_timestamp": message.get("edited_timestamp"),
    }
