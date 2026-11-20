from dataclasses import dataclass

from .channel import Channel


@dataclass
class Guild:
    id: str
    name: str
    channels: list[Channel]


__all__ = ["Guild"]
