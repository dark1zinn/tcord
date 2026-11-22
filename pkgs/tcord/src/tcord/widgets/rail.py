"""Server rail: DM home plus one icon per guild, with Discord-style unread pills and badges."""

from __future__ import annotations

from client.cdn import guild_icon_url
from client.models import Guild
from client.state import ChatStore
from rich.text import Text
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option, OptionDoesNotExist

from ..formatting import guild_abbreviation, icons
from ..i18n import _
from ..images import RGB, ImageKey, ImageStore

HOME = "home"
ICON_COLS = 6
ICON_ROWS = 3
ICON_PIXELS = 64


def _badge(mentions: int) -> str:
    return f" {mentions if mentions < 100 else '99+'} "


class ServerRail(OptionList):
    """DM home followed by one icon per guild.

    Like Discord's client: a pill on the left edge marks unread servers (short) and the
    selected server (tall); a red badge in the icon's corner counts mentions.
    """

    class GuildChosen(Message):
        def __init__(self, guild_id: str | None) -> None:
            super().__init__()
            self.guild_id = guild_id

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id, markup=False)
        self._order: list[str | None] = []
        self.store: ChatStore | None = None
        self.images: ImageStore | None = None
        self.active: str | None = None
        self.ascii_only = False
        self.panel: RGB = (30, 31, 34)
        self.styles_map = {
            "badge": "bold white on #f23f43",
            "pill": "bold white",
            "home": "bold white on #5865f2",
            "tile": "bold on #313338",
        }

    def configure(
        self,
        store: ChatStore,
        images: ImageStore,
        *,
        ascii_only: bool,
        panel: RGB,
        accent: str,
        error: str,
        tile: str,
    ) -> None:
        self.store, self.images, self.ascii_only, self.panel = store, images, ascii_only, panel
        self.styles_map = {
            "badge": f"bold white on {error}",
            "pill": "bold",
            "home": f"bold white on {accent}",
            "tile": f"bold on {tile}",
        }

    # ----- building --------------------------------------------------------------------------

    def rebuild(self, active_guild: str | None) -> None:
        store = self.store
        if store is None:
            return
        self.active = active_guild
        self._order = [None, *store.guilds]
        options: list[Option | None] = [self._option(None)]
        if len(self._order) > 1:
            options.append(None)  # separator under the DM home, like Discord
        options.extend(self._option(guild_id) for guild_id in self._order[1:])
        highlighted = self.highlighted
        self.set_options(options)
        self.highlighted = highlighted if highlighted is not None else self._index(active_guild)

    def refresh_badges(self) -> None:
        for guild_id in self._order:
            option = self._option(guild_id)
            if option.id is not None:
                self.replace_option_prompt(option.id, option.prompt)

    def images_ready(self, urls: set[str]) -> None:
        store = self.store
        if store is None:
            return
        for guild_id in self._order[1:]:
            guild = store.guilds.get(guild_id or "")
            if guild is not None and guild.icon and self._icon_url(guild) in urls:
                option = self._option(guild_id)
                if option.id is not None:
                    self.replace_option_prompt(option.id, option.prompt)

    def set_active(self, guild_id: str | None) -> None:
        previous, self.active = self.active, guild_id
        for changed in {previous, guild_id}:
            option = self._option(changed)
            if option.id is not None:
                self.replace_option_prompt(option.id, option.prompt)
        self.highlighted = self._index(guild_id)

    # ----- rendering -------------------------------------------------------------------------

    def _icon_url(self, guild: Guild) -> str:
        assert guild.icon is not None
        return guild_icon_url(guild.id, guild.icon, ICON_PIXELS)

    def _option(self, guild_id: str | None) -> Option:
        store = self.store
        assert store is not None
        if guild_id is None:
            unread, mentions = store.dm_badge()
            tile = self._tile(_("DMs") if self.ascii_only else icons(False).dm, home=True)
            return Option(self._render_option(tile, unread, mentions, guild_id), id=HOME)
        guild = store.guilds[guild_id]
        unread, mentions = store.guild_badge(guild_id)
        tile = self._guild_tile(guild)
        return Option(self._render_option(tile, unread, mentions, guild_id), id=f"g{guild_id}")

    def _guild_tile(self, guild: Guild) -> list[Text]:
        images = self.images
        if images is not None and guild.icon and not guild.unavailable:
            key = ImageKey(self._icon_url(guild), ICON_COLS, ICON_ROWS, self.panel, exact=True)
            rendered = images.get(key)
            if rendered is not None:
                return list(rendered.frame().split("\n"))
        tile = self._tile(guild_abbreviation(guild.name))
        if guild.unavailable:
            for line in tile:
                line.stylize("dim")
        return tile

    def _tile(self, label: str, *, home: bool = False) -> list[Text]:
        style = self.styles_map["home" if home else "tile"]
        middle = ICON_ROWS // 2
        return [
            Text((label if row == middle else "")[:ICON_COLS].center(ICON_COLS), style=style)
            for row in range(ICON_ROWS)
        ]

    def _render_option(
        self, tile: list[Text], unread: bool, mentions: int, guild_id: str | None
    ) -> Text:
        active = guild_id == self.active
        pill = "|" if self.ascii_only else "▌"
        middle = ICON_ROWS // 2
        result = Text(no_wrap=True, overflow="crop")
        for row, line in enumerate(tile):
            show_pill = active or (unread and row == middle)
            result.append(pill if show_pill else " ", self.styles_map["pill"])
            if mentions and row == ICON_ROWS - 1:
                badge = _badge(mentions)
                keep = max(0, ICON_COLS - len(badge))
                result.append_text(line[:keep])
                result.append(badge[:ICON_COLS], self.styles_map["badge"])
            else:
                result.append_text(line)
            if row < ICON_ROWS - 1:
                result.append("\n")
        return result

    # ----- navigation ------------------------------------------------------------------------

    def _index(self, guild_id: str | None) -> int:
        try:
            return self.get_option_index(HOME if guild_id is None else f"g{guild_id}")
        except OptionDoesNotExist:
            return 0

    def guild_at(self, index: int) -> str | None:
        option = self.get_option_at_index(index)
        if option.id is None or option.id == HOME:
            return None
        return option.id[1:]

    def index_of(self, guild_id: str | None) -> int:
        return self._index(guild_id)

    @property
    def guild_ids(self) -> list[str | None]:
        return list(self._order)

    @property
    def count(self) -> int:
        return len(self._order)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.post_message(self.GuildChosen(self.guild_at(event.option_index)))
