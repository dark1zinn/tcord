"""Quick switcher: fuzzy jump to channels, DMs and servers via the command palette."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from textual.command import DiscoveryHit, Hit, Hits, Provider

from .formatting import dm_name

if TYPE_CHECKING:
    from .app import TcordApp

DISCOVERY_LIMIT = 30


@dataclass(frozen=True, slots=True)
class Target:
    text: str
    unread: bool
    open: Callable[[], None]


class ChannelSwitcherProvider(Provider):
    def _targets(self) -> list[Target]:
        app = cast("TcordApp", self.app)
        chat = app.chat_screen
        if chat is None:
            return []
        store = app.store
        targets: list[Target] = []
        for channel in store.private_channels():
            targets.append(
                Target(
                    "@" + dm_name(channel, store.me),
                    store.is_unread(channel.id),
                    lambda cid=channel.id: chat.jump_to(None, cid),
                )
            )
        for guild in store.guilds.values():
            targets.append(
                Target(
                    guild.name,
                    store.guild_badge(guild.id)[0],
                    lambda gid=guild.id: chat.jump_to(gid, None),
                )
            )
            for channel in store.guild_channels(guild.id):
                if channel.is_readable:
                    targets.append(
                        Target(
                            f"#{channel.name} ({guild.name})",
                            store.is_unread(channel.id),
                            lambda gid=guild.id, cid=channel.id: chat.jump_to(gid, cid),
                        )
                    )
        return targets

    async def discover(self) -> Hits:
        targets = sorted(self._targets(), key=lambda target: not target.unread)
        for target in targets[:DISCOVERY_LIMIT]:
            yield DiscoveryHit(target.text, target.open, text=target.text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for target in self._targets():
            score = matcher.match(target.text)
            if score > 0:
                if target.unread:
                    score = min(1.0, score + 0.05)
                yield Hit(score, matcher.highlight(target.text), target.open, text=target.text)
