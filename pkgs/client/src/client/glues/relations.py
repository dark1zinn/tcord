from typing import Any

from ..models import Channel, Guild
from ..protocols import RelationsProtocol


class Relations(RelationsProtocol):
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def get_guilds(self) -> list[Guild]:
        return [Guild(**guild) for guild in await self._engine.get_guilds()]

    async def get_channels(self, guild_id: str) -> list[Channel]:
        channels = await self._engine.get_channels(guild_id)
        return [Channel(**channel) for channel in channels]

    async def get_dms(self) -> list[Channel]:
        return [Channel(**channel) for channel in await self._engine.get_dms()]
