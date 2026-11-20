import tcord_core

from ..models import MessageInfo, MessageReference
from ..protocols import MessagesProtocol
from . import convert


class Messages(MessagesProtocol):
    def __init__(self, engine: tcord_core.Session) -> None:
        self._engine = engine

    async def history(
        self, channel_id: str, before: str | None = None, limit: int = 50
    ) -> list[MessageInfo]:
        messages = await self._engine.fetch_messages(channel_id, before, limit)
        return [convert.message(message) for message in messages]

    async def send(
        self,
        channel_id: str,
        content: str,
        *,
        reply_to: MessageReference | None = None,
        nonce: str | None = None,
    ) -> MessageInfo:
        reference = (
            tcord_core.MessageReference(reply_to.message_id, reply_to.channel_id, reply_to.guild_id)
            if reply_to is not None
            else None
        )
        sent = await self._engine.send_message(channel_id, content, reference, nonce)
        return convert.message(sent)
