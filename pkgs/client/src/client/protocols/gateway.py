from typing import Protocol

from ..models import Channel, Guild


class GatewayProtocol(Protocol):
    async def guilds(self) -> list[Guild]: ...

    async def guild_channels(self, guild_id: str) -> list[Channel]: ...

    async def private_channels(self) -> list[Channel]: ...
