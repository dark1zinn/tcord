from typing import Protocol


class MediaProtocol(Protocol):
    async def fetch(self, url: str) -> bytes:
        """Bytes of a Discord CDN or media-proxy URL; other hosts are refused."""
        ...
