import tcord_core

from ..protocols import MediaProtocol


class Media(MediaProtocol):
    def __init__(self, engine: tcord_core.Session) -> None:
        self._engine = engine

    async def fetch(self, url: str) -> bytes:
        return await self._engine.fetch_media(url)
