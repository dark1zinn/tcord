import tcord_core

from ..models import Channel, Guild
from ..protocols import GatewayProtocol
from . import convert


class Gateway(GatewayProtocol):
    """Guild and channel directory. REST fetchers double as gateway fallbacks."""

    def __init__(self, engine: tcord_core.Session) -> None:
        self._engine = engine

    async def guilds(self) -> list[Guild]:
        return [convert.guild(guild) for guild in await self._engine.fetch_guilds()]

    async def guild_channels(self, guild_id: str) -> list[Channel]:
        channels = await self._engine.fetch_guild_channels(guild_id)
        return [convert.channel(channel) for channel in channels]

    async def private_channels(self) -> list[Channel]:
        channels = await self._engine.fetch_private_channels()
        return [convert.channel(channel) for channel in channels]
