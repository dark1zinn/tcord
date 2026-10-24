from typing import Protocol

from ..models import Channel, Guild


class RelationsProtocol(Protocol):
    async def get_guilds(self) -> list[Guild]: ...

    async def get_channels(self, guild_id: str) -> list[Channel]: ...

    async def get_dms(self) -> list[Channel]: ...
