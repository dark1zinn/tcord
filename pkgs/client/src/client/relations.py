from typing import List, Any

from .models import Channel, Guild, User
from .protocols import RelationsProtocol

class Relations(RelationsProtocol):
    def __init__(self, engine: Any):
        self._engine = engine

    async def get_guilds(self) -> List[Guild]:
        # TODO: Fetch from engine, map to Guild dataclass
        return []

    async def get_channels(self, guild_id: str) -> List[Channel]:
        return []

    async def get_dms(self) -> List[Channel]:
        return []

    # --- Deferred Implementations ---
    async def get_friends(self) -> List[User]:
        raise NotImplementedError()
    async def add_friend(self, user_id: str) -> bool:
        raise NotImplementedError()
    async def remove_friend(self, user_id: str) -> bool:
        raise NotImplementedError()
    async def join_guild(self, invite_code: str) -> Guild:
        raise NotImplementedError()
    async def leave_guild(self, guild_id: str) -> bool:
        raise NotImplementedError()
