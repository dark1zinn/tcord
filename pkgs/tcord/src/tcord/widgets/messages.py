from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from client.cdn import avatar_url
from client.models import MessageInfo
from client.state import ChannelHistory, ChatStore
from rich.console import RenderableType
from rich.padding import Padding
from rich.style import Style
from rich.styled import Styled
from rich.table import Table
from rich.text import Text
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option, OptionDoesNotExist

from ..formatting import day_label, format_time, human_size, icons
from ..i18n import _
from ..images import ImageKey, ImageStore
from ..markdown import RenderContext, plain, render

GROUP_WINDOW = timedelta(minutes=7)
TOP = "top"
AVATAR_COLS = 4
AVATAR_ROWS = 2
AVATAR_PIXELS = 64


@dataclass(frozen=True, slots=True)
class ViewOptions:
    density: str = "cozy"
    show_timestamps: bool = True
    time_format: str = "24h"
    ascii_only: bool = False
    me_id: str | None = None
    mention_style: str = "on #3f3a2e"
    show_avatars: bool = True
    #: Message list background, used to flatten transparent images.
    background: tuple[int, int, int] = (49, 51, 56)
    #: Placeholder tile for avatars that are not (yet) loaded.
    avatar_style: str = "bold on #4e5058"


def _option_id(message_id: str) -> str:
    return "m" + message_id


def _groups_with(previous: MessageInfo | None, message: MessageInfo) -> bool:
    return (
        previous is not None
        and previous.author.id == message.author.id
        and message.referenced is None
        and message.timestamp - previous.timestamp <= GROUP_WINDOW
        and message.timestamp.astimezone().date() == previous.timestamp.astimezone().date()
    )


class MessageList(OptionList):
    """Messages of the active channel, with day dividers and a top paging sentinel."""

    class LoadOlder(Message):
        pass

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id, markup=False)
        self.messages: list[MessageInfo] = []
        self.view = ViewOptions()
        self.context = RenderContext()
        self.channel_label = ""
        self.has_more_before = False
        self.store: ChatStore | None = None
        self.images: ImageStore | None = None
        self._cache: dict[str, tuple[tuple, RenderableType]] = {}
        self._image_owners: dict[str, set[str]] = {}

    # ----- building --------------------------------------------------------------------------

    def show(
        self,
        history: ChannelHistory | None,
        channel_label: str,
        view: ViewOptions,
        context: RenderContext,
        *,
        keep_position: bool = False,
    ) -> None:
        """Rebuild every option (channel switch, paging, removal, settings change)."""
        anchor = self.highlighted_message() if keep_position else None
        at_bottom = self.is_vertical_scroll_end or not self.messages
        if view != self.view or context != self.context:
            self._cache.clear()
        self.view, self.context, self.channel_label = view, context, channel_label
        self.messages = list(history.messages) if history else []
        self.has_more_before = bool(history and history.has_more_before)
        current = {message.id for message in self.messages}
        self._cache = {key: value for key, value in self._cache.items() if key in current}
        self._image_owners = {
            url: owners & current for url, owners in self._image_owners.items() if owners & current
        }
        options = [self._sentinel()]
        previous: MessageInfo | None = None
        for message in self.messages:
            if previous is None or self._new_day(previous, message):
                options.append(Option(Text(day_label(message.timestamp), style="dim"), None, True))
            options.append(self._option(message, previous))
            previous = message
        self.set_options(options)
        if anchor is not None and self._index(anchor.id) is not None:
            self.highlighted = self._index(anchor.id)
        elif not keep_position or at_bottom:
            self.highlighted = self.option_count - 1 if self.messages else None
            self.scroll_end(animate=False)

    def append(self, message: MessageInfo) -> bool:
        """Add one message at the end; returns whether the view stayed at the bottom."""
        at_bottom = self.is_vertical_scroll_end
        previous = self.messages[-1] if self.messages else None
        self.messages.append(message)
        if previous is None or self._new_day(previous, message):
            self.add_option(Option(Text(day_label(message.timestamp), style="dim"), None, True))
        self.add_option(self._option(message, previous))
        if at_bottom:
            self.highlighted = self.option_count - 1
            self.scroll_end(animate=False)
        return at_bottom

    def update(self, message: MessageInfo) -> None:
        position = next((i for i, m in enumerate(self.messages) if m.id == message.id), None)
        if position is None:
            return
        self.messages[position] = message
        previous = self.messages[position - 1] if position else None
        option = self._option(message, previous)
        self.replace_option_prompt(_option_id(message.id), option.prompt)

    def trim(self, limit: int) -> None:
        """Drop the oldest messages beyond ``limit`` without rebuilding the whole list."""
        if len(self.messages) <= limit:
            return
        while len(self.messages) > limit:
            oldest = self.messages.pop(0)
            self.remove_option(_option_id(oldest.id))
            following = self.get_option_at_index(2) if self.option_count > 2 else None
            if following is not None and following.id is None:
                self.remove_option_at_index(1)  # day divider left without messages
        self.has_more_before = True
        self.replace_option_prompt(TOP, self._sentinel().prompt)
        first = self.messages[0]
        self.replace_option_prompt(_option_id(first.id), self._option(first, None).prompt)

    # ----- rendering -------------------------------------------------------------------------

    def _sentinel(self) -> Option:
        if self.has_more_before:
            label = _("Loading older messages…")
        else:
            label = _("This is the beginning of {channel}").format(channel=self.channel_label)
        return Option(Text(label, style="dim italic"), TOP, True)

    @staticmethod
    def _new_day(previous: MessageInfo, message: MessageInfo) -> bool:
        return message.timestamp.astimezone().date() != previous.timestamp.astimezone().date()

    def _option(self, message: MessageInfo, previous: MessageInfo | None) -> Option:
        grouped = self.view.density == "cozy" and _groups_with(previous, message)
        spaced = self.view.density == "cozy" and not grouped and previous is not None
        signature = (
            message.content,
            message.edited_timestamp,
            message.pending,
            message.failed,
            grouped,
            spaced,
            self._name_color(message),
        )
        cached = self._cache.get(message.id)
        if cached is not None and cached[0] == signature:
            return Option(cached[1], _option_id(message.id))
        prompt = self._render_message(message, grouped, spaced)
        self._cache[message.id] = (signature, prompt)
        return Option(prompt, _option_id(message.id))

    def _name_color(self, message: MessageInfo) -> int | None:
        store = self.store
        if store is None or message.guild_id is None:
            return None
        return store.role_color(message.guild_id, message.author.id)

    def _mentions_me(self, message: MessageInfo) -> bool:
        me = self.view.me_id
        if me is None or message.author.id == me:
            return False
        replied_to_me = message.referenced is not None and message.referenced.author.id == me
        return message.mention_everyone or me in message.mention_ids or replied_to_me

    def _author_style(self, message: MessageInfo) -> Style:
        color = self._name_color(message)
        if color:
            return Style(bold=True, color=f"#{color & 0xFFFFFF:06x}")
        return Style(bold=True)

    def _avatar(self, message: MessageInfo) -> Text:
        """The author's avatar, or an initial tile until it loads (or if it cannot)."""
        images = self.images
        if images is not None:
            url = avatar_url(message.author, message.member, message.guild_id, AVATAR_PIXELS)
            self._track(url, message.id)
            key = ImageKey(url, AVATAR_COLS, AVATAR_ROWS, self.view.background, exact=True)
            rendered = images.get(key)
            if rendered is not None:
                return rendered.frame()
        initial = (message.author_name[:1] or "?").upper()
        style = self.view.avatar_style
        return Text(f" {initial}  \n    ", style=style, no_wrap=True)

    @property
    def _avatar_column(self) -> bool:
        view = self.view
        images = self.images
        return (
            view.density == "cozy" and view.show_avatars and images is not None and images.enabled
        )

    def _track(self, url: str, message_id: str) -> None:
        self._image_owners.setdefault(url, set()).add(message_id)

    def _render_message(self, message: MessageInfo, grouped: bool, spaced: bool) -> RenderableType:
        view = self.view
        body = self._body(message, grouped)
        if self._avatar_column:
            avatar = None if grouped else self._avatar(message)
            grid = Table.grid(padding=(0, 1), expand=True)
            grid.add_column(width=AVATAR_COLS, no_wrap=True)
            grid.add_column(ratio=1)
            grid.add_row(avatar or Text(" " * AVATAR_COLS), body)
            content: RenderableType = grid
        else:
            content = body
        style = ""
        if self._mentions_me(message):
            style = view.mention_style
        if message.pending and not message.failed:
            style = f"{style} dim".strip()
        if style:
            content = Styled(content, style)
        return Padding(content, (1, 0, 0, 0)) if spaced else content

    def _body(self, message: MessageInfo, grouped: bool) -> Text:
        view, glyphs = self.view, icons(self.view.ascii_only)
        text = Text()
        if message.referenced is not None:
            reply = message.referenced
            snippet = plain(reply.content, self.context).replace("\n", " ")[:80]
            text.append(f"{glyphs.reply} @{reply.author_name} {snippet}\n", style="dim")
        body = render(message.content, self.context)
        time_text = format_time(message.timestamp, view.time_format)
        edited = _("(edited)") if message.edited_timestamp else ""
        if view.density == "compact":
            if view.show_timestamps:
                text.append(time_text + " ", style="dim")
            text.append(message.author_name, style=self._author_style(message))
            text.append(": ")
            text.append_text(body)
            if edited:
                text.append(" " + edited, style="dim")
        else:
            if not grouped:
                text.append(message.author_name, style=self._author_style(message))
                if view.show_timestamps:
                    text.append("  " + time_text, style="dim")
                text.append("\n")
            text.append_text(body)
            if edited:
                text.append(" " + edited, style="dim")
        for attachment in message.attachments:
            text.append(f"\n{glyphs.attachment} ")
            link = f"underline link {attachment.url}" if attachment.url else ""
            text.append(attachment.filename, style=link)
            text.append(f" ({human_size(attachment.size)})", style="dim")
        for embed in message.embeds:
            if embed.title:
                text.append(f"\n{glyphs.embed}{embed.title}", style="italic")
        if message.failed:
            text.stylize("red")
            text.append("\n" + _("(failed — press R to retry)"), style="bold red")
        return text

    def images_ready(self, urls: set[str]) -> None:
        """Re-render the messages showing any of ``urls``."""
        owners: set[str] = set()
        for url in urls:
            owners |= self._image_owners.get(url, set())
        for position, message in enumerate(self.messages):
            if message.id in owners:
                self._cache.pop(message.id, None)
                previous = self.messages[position - 1] if position else None
                option = self._option(message, previous)
                self.replace_option_prompt(option.id or "", option.prompt)

    # ----- queries ---------------------------------------------------------------------------

    def _index(self, message_id: str) -> int | None:
        try:
            return self.get_option_index(_option_id(message_id))
        except OptionDoesNotExist:
            return None

    def highlighted_message(self) -> MessageInfo | None:
        if self.highlighted is None:
            return None
        option = self.get_option_at_index(self.highlighted)
        if option.id is None or not option.id.startswith("m"):
            return None
        message_id = option.id[1:]
        return next((m for m in self.messages if m.id == message_id), None)

    def last_visible_is_latest(self) -> bool:
        return bool(self.messages) and self.is_vertical_scroll_end

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        first_message_index = 2  # sentinel + first day divider
        if self.has_more_before and event.option_index <= first_message_index:
            self.post_message(self.LoadOlder())

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
