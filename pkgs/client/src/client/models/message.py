from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MessageInfo:
    """The stable, presentation-safe subset of a Discord message."""

    id: str
    channel_id: str
    author_id: str
    author: str
    content: str
    timestamp: str
    guild_id: str | None = None
    edited_timestamp: str | None = None

    @classmethod
    def from_wire(cls, value: dict[str, Any]) -> "MessageInfo":
        return cls(
            id=str(value["id"]),
            channel_id=str(value["channel_id"]),
            author_id=str(value["author_id"]),
            author=str(value["author"]),
            content=str(value.get("content", "")),
            timestamp=str(value["timestamp"]),
            guild_id=(
                str(value["guild_id"]) if value.get("guild_id") is not None else None
            ),
            edited_timestamp=(
                str(value["edited_timestamp"])
                if value.get("edited_timestamp") is not None
                else None
            ),
        )


__all__ = ["MessageInfo"]
