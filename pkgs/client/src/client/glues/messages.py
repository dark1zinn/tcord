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

    async def edit(self, channel_id: str, message_id: str, content: str) -> MessageInfo:
        return convert.message(await self._engine.edit_message(channel_id, message_id, content))

    async def delete(self, channel_id: str, message_id: str) -> None:
        await self._engine.delete_message(channel_id, message_id)

    async def trigger_typing(self, channel_id: str) -> None:
        await self._engine.trigger_typing(channel_id)

    async def ack(self, channel_id: str, message_id: str) -> None:
        await self._engine.ack_message(channel_id, message_id)
