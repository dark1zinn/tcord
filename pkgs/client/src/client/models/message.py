from dataclasses import dataclass

from .user import User

@dataclass
class Message:
    id: str
    channel_id: str
    content: str
    author: User
