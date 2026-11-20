from typing import Protocol

from ..models import MessageInfo, MessageReference


class MessagesProtocol(Protocol):
    async def history(
        self, channel_id: str, before: str | None = None, limit: int = 50
    ) -> list[MessageInfo]: ...

    async def send(
        self,
        channel_id: str,
        content: str,
        *,
        reply_to: MessageReference | None = None,
        nonce: str | None = None,
    ) -> MessageInfo: ...
