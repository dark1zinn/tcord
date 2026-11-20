import asyncio
import json
from typing import Any

from ..models import Channel, Guild
from ..protocols import RelationsProtocol


class Relations(RelationsProtocol):
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def get_guilds(self) -> list[Guild]:
        guilds = json.loads(await asyncio.to_thread(self._engine.get_guilds))
        return [
            Guild(id=str(guild["id"]), name=str(guild["name"]), channels=[]) for guild in guilds
        ]

    async def get_channels(self, guild_id: str) -> list[Channel]:
        channels = json.loads(await asyncio.to_thread(self._engine.get_channels, guild_id))
        return [
            Channel(id=str(channel["id"]), name=str(channel["name"]), is_dm=False)
            for channel in channels
        ]

    async def get_dms(self) -> list[Channel]:
        channels = json.loads(await asyncio.to_thread(self._engine.get_dms))
        return [
            Channel(
                id=str(channel["id"]),
                name=str(channel.get("name") or _dm_name(channel)),
                is_dm=True,
            )
            for channel in channels
        ]


def _dm_name(channel: dict[str, Any]) -> str:
    recipients = channel.get("recipients") or []
    return (
        ", ".join(str(recipient.get("username", "Unknown")) for recipient in recipients)
        or "Direct message"
    )
