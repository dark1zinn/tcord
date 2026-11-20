from __future__ import annotations

from client.state import ChatStore
from rich.text import Text
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from ..formatting import guild_abbreviation, icons
from ..i18n import _

HOME = "home"


class ServerRail(OptionList):
    """Direct messages home followed by one entry per guild, with unread/mention badges."""

    class GuildChosen(Message):
        def __init__(self, guild_id: str | None) -> None:
            super().__init__()
            self.guild_id = guild_id

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id, markup=False)
        self._order: list[str | None] = []

    def rebuild(self, store: ChatStore, active_guild: str | None, ascii_only: bool) -> None:
        highlighted = self.highlighted
        self._order = [None, *store.guilds]
        self.set_options(self._option(store, guild_id, ascii_only) for guild_id in self._order)
        index = self._order.index(active_guild) if active_guild in self._order else 0
        self.highlighted = highlighted if highlighted is not None else index

    def refresh_badges(self, store: ChatStore, ascii_only: bool) -> None:
        for guild_id in self._order:
            option = self._option(store, guild_id, ascii_only)
            if option.id is not None:
                self.replace_option_prompt(option.id, option.prompt)

    def _option(self, store: ChatStore, guild_id: str | None, ascii_only: bool) -> Option:
        if guild_id is None:
            unread, mentions = store.dm_badge()
            label, option_id = _("DMs"), HOME
        else:
            guild = store.guilds[guild_id]
            unread, mentions = store.guild_badge(guild_id)
            label, option_id = guild_abbreviation(guild.name), f"g{guild_id}"
        prompt = Text()
        prompt.append(icons(ascii_only).unread if unread else " ", style="bold")
        prompt.append(label, style="bold" if unread else "")
        if mentions:
            prompt.append(f" {mentions}", style="bold reverse")
        return Option(prompt, id=option_id)

    def guild_at(self, index: int) -> str | None:
        return self._order[index] if 0 <= index < len(self._order) else None

    def index_of(self, guild_id: str | None) -> int:
        return self._order.index(guild_id) if guild_id in self._order else 0

    @property
    def count(self) -> int:
        return len(self._order)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.post_message(self.GuildChosen(self.guild_at(event.option_index)))
