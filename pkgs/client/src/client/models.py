from dataclasses import dataclass
from typing import List

@dataclass
class User:
    id: str
    username: str

@dataclass
class Message:
    id: str
    channel_id: str
    content: str
    author: User

@dataclass
class Channel:
    id: str
    name: str
    is_dm: bool

@dataclass
class Guild:
    id: str
    name: str
    channels: List[Channel]
