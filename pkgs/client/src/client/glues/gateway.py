import tcord_core

from ..models import Channel, GatewayEvent, Guild, ReadySnapshot
from ..protocols import GatewayProtocol
from . import convert


class Gateway(GatewayProtocol):
    """Live gateway plus REST directory fetchers (used for refresh and unavailable guilds)."""

    def __init__(self, engine: tcord_core.Session) -> None:
        self._engine = engine

    async def connect(self) -> ReadySnapshot:
        return convert.ready(await self._engine.connect_gateway())

    async def next_event(self) -> GatewayEvent | None:
        """Next event, or ``None`` once the gateway stopped (logout, close, fatal error)."""
        while True:
            native = await self._engine.next_event()
            if native is None:
                return None
            event = convert.event(native)
            if event is not None:
                return event

    def subscribe_guild(self, guild_id: str) -> None:
        self._engine.subscribe_guild(guild_id)

    def disconnect(self) -> None:
        self._engine.disconnect_gateway()

    async def guilds(self) -> list[Guild]:
        return [convert.guild(guild) for guild in await self._engine.fetch_guilds()]

    async def guild_channels(self, guild_id: str) -> list[Channel]:
        channels = await self._engine.fetch_guild_channels(guild_id)
        return [convert.channel(channel) for channel in channels]

    async def private_channels(self) -> list[Channel]:
        channels = await self._engine.fetch_private_channels()
        return [convert.channel(channel) for channel in channels]
