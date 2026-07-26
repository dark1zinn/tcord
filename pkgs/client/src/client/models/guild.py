from dataclasses import dataclass
from typing import List

from .channel import Channel

@dataclass
class Guild:
    id: str
    name: str
    channels: List[Channel]

__all__ = [
    'Guild'
]
