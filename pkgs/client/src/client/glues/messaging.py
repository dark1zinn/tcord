from typing import Any, Callable, Awaitable, List
import asyncio

from ..protocols import MessagingProtocol
from ..models import Message

class Messaging(MessagingProtocol):
    def __init__(self, engine: Any):
        self._engine = engine
        self._message_callbacks: List[Callable[[Message], Awaitable[None]]] = []

    async def send_message(self, channel_id: str, content: str) -> bool:
        # TODO: Call engine.send_message()
        return True

    def on_message_received(self, callback: Callable[[Message], Awaitable[None]]) -> None:
        """Registers a callback for the TUI to hook into."""
        self._message_callbacks.append(callback)

    async def _dispatch_internal_event(self, raw_message_data: dict) -> None:
        """Internal method called by the background event loop when Rust yields a message."""
        # Convert raw dict to dataclass
        msg = Message(...)
        # Fire all registered TUI callbacks
        await asyncio.gather(*(cb(msg) for cb in self._message_callbacks))

    # --- Deferred Implementations ---
    async def edit_message(self, channel_id: str, message_id: str, new_content: str) -> bool:
        raise NotImplementedError()
    async def delete_message(self, channel_id: str, message_id: str) -> bool:
        raise NotImplementedError()
