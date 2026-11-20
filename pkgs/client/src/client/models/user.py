from dataclasses import dataclass


@dataclass
class User:
    id: str
    username: str


__all__ = ["User"]
