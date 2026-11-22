from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from urllib.parse import urlsplit

from client.cdn import (
    attachment_preview_url,
    avatar_url,
    embed_media_url,
    emoji_url,
    sticker_url,
)
from client.models import Attachment, Channel, Embed, EmbedMedia, MessageInfo, StickerFormat
from client.state import ChannelHistory, ChatStore
from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.style import Style
from rich.styled import Styled
from rich.table import Table
from rich.text import Text
from textual.message import Message
from textual.timer import Timer
from textual.widgets import OptionList
from textual.widgets.option_list import Option, OptionDoesNotExist

from ..formatting import day_label, format_time, human_size, icons
from ..i18n import _, ngettext
from ..images import ImageKey, ImageStore, RenderedImage, fit
from ..markdown import CUSTOM_EMOJI, JUMBO_LIMIT, RenderContext, plain, render

GROUP_WINDOW = timedelta(minutes=7)
TOP = "top"
AVATAR_COLS = 4
AVATAR_ROWS = 2
AVATAR_PIXELS = 64
EMOJI_PIXELS = 48
JUMBO_EMOJI = (4, 2)
STICKER_CELLS = (16, 8)
STICKER_PIXELS = 160
MEDIA_CELLS = (40, 15)
EMBED_IMAGE_CELLS = (24, 8)
EMBED_LINES = 6
FRAME_SECONDS = 0.125


@dataclass(frozen=True, slots=True)
class ViewOptions:
    density: str = "cozy"
    show_timestamps: bool = True
    time_format: str = "24h"
    ascii_only: bool = False
    me_id: str | None = None
    mention_style: str = "on #3f3a2e"
    show_avatars: bool = True
    show_images: bool = True
    animate_media: bool = True
    #: Message list background, used to flatten transparent images.
    background: tuple[int, int, int] = (49, 51, 56)
    #: Placeholder tile for avatars that are not (yet) loaded.
    avatar_style: str = "bold on #4e5058"
    #: Active image renderer; switching it must re-render every cached message.
    image_protocol: str = "off"


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


def _frame_at(rendered: RenderedImage, elapsed: float) -> int:
    total = sum(frame.seconds for frame in rendered.frames) or 1.0
    position = elapsed % total
    for index, frame in enumerate(rendered.frames):
        position -= frame.seconds
        if position < 0:
            return index
    return 0


def _animated_hash(image_hash: str | None) -> bool:
    return bool(image_hash and image_hash.startswith("a_"))


def _is_gif(media: EmbedMedia | None) -> bool:
    """Embed media that is a (possibly animated) GIF, judging by its URL."""
    if media is None:
        return False
    urls = (media.url, media.proxy_url)
    return any(urlsplit(url).path.lower().endswith(".gif") for url in urls if url)


def _embed_media(embed: Embed) -> EmbedMedia | None:
    """The picture an image or GIF embed shows (GIF embeds prefer the thumbnail)."""
    if embed.kind == "gifv":
        return embed.thumbnail or embed.image
    return embed.image or embed.thumbnail


def _has_motion(message: MessageInfo) -> bool:
    """Whether any media of the message may animate."""
    member_avatar = message.member.avatar if message.member else None
    return (
        _animated_hash(member_avatar)
        or _animated_hash(message.author.avatar)
        or any(match.group(1) == "a" for match in CUSTOM_EMOJI.finditer(message.content))
        or any(
            sticker.format in (StickerFormat.GIF, StickerFormat.APNG)
            for sticker in message.stickers
        )
        or any(attachment.content_type == "image/gif" for attachment in message.attachments)
        or any(_is_gif(_embed_media(embed)) for embed in message.embeds)
    )


def _hstack(tiles: list[Text], rows: int) -> Text:
    """Place multi-line tiles side by side with one space between them."""
    lines = [Text() for _ in range(rows)]
    for index, tile in enumerate(tiles):
        for row, line in enumerate(tile.split("\n")[:rows]):
            if index:
                lines[row].append(" ")
            lines[row].append_text(line)
    return Text("\n").join(lines)


class MessageList(OptionList):
    """Messages of the active channel, with day dividers and a top paging sentinel."""

    class LoadOlder(Message):
        pass

    class ThreadOpened(Message):
        """Enter on a message that started a thread."""

        def __init__(self, thread: Channel) -> None:
            super().__init__()
            self.thread = thread

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
        self._animating: str | None = None
        self._animation_start = 0.0
        self._animation_timer: Timer | None = None
        #: Messages whose kitty images were evicted; they reload once on screen again.
        self._stale: set[str] = set()
        self._defer_loads = False

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
        self._stale.clear()  # every message is rendered (and its images loaded) below
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
            self._forget(oldest.id)
            following = self.get_option_at_index(2) if self.option_count > 2 else None
            if following is not None and following.id is None:
                self.remove_option_at_index(1)  # day divider left without messages
        self.has_more_before = True
        self.replace_option_prompt(TOP, self._sentinel().prompt)
        first = self.messages[0]
        self.replace_option_prompt(_option_id(first.id), self._option(first, None).prompt)

    def _forget(self, message_id: str) -> None:
        """Drop the cached prompt and image ownership of a message no longer listed."""
        self._cache.pop(message_id, None)
        self._stale.discard(message_id)
        for url in [url for url, owners in self._image_owners.items() if message_id in owners]:
            owners = self._image_owners[url]
            owners.discard(message_id)
            if not owners:
                del self._image_owners[url]

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
        message = self._resolved(message)
        grouped = self.view.density == "cozy" and _groups_with(previous, message)
        spaced = self.view.density == "cozy" and not grouped and previous is not None
        animating = message.id == self._animating
        signature = (
            message.content,
            message.edited_timestamp,
            message.pending,
            message.failed,
            message.member,
            message.attachments,
            message.embeds,
            message.stickers,
            message.thread,
            message.referenced,
            grouped,
            spaced,
            self._name_color(message),
            animating,
        )
        cached = self._cache.get(message.id)
        if cached is not None and cached[0] == signature and not animating:
            return Option(cached[1], _option_id(message.id))
        prompt = self._render_message(message, grouped, spaced)
        self._cache[message.id] = (signature, prompt)
        return Option(prompt, _option_id(message.id))

    def _resolved(self, message: MessageInfo) -> MessageInfo:
        """Fill the guild (REST history omits it) and the author's current member data."""
        store = self.store
        if store is None:
            return message
        guild_id = message.guild_id
        if guild_id is None:
            channel = store.channels.get(message.channel_id)
            guild_id = channel.guild_id if channel is not None else None
        if guild_id is None:
            return message
        # The store holds the latest member (role and nickname updates); the copy carried by
        # the message is only a fallback.
        member = store.member(guild_id, message.author.id) or message.member
        if guild_id == message.guild_id and member == message.member:
            return message
        return replace(message, guild_id=guild_id, member=member)

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

    # ----- images ----------------------------------------------------------------------------

    def _image(
        self,
        message_id: str,
        url: str,
        max_cols: int,
        max_rows: int,
        *,
        exact: bool = False,
        animated: bool = False,
        media: bool = True,
    ) -> Text | None:
        """A loaded image (current animation frame when animating), else ``None``."""
        images = self.images
        view = self.view
        if images is None or not images.enabled or (media and not view.show_images):
            return None
        self._track(url, message_id)
        animate = animated and view.animate_media and message_id == self._animating
        key = ImageKey(url, max_cols, max_rows, view.background, animate=animate, exact=exact)
        rendered = images.get(key, load=not self._defer_loads)
        if rendered is None and animate:
            # Until the animated version is decoded, keep showing the still image.
            rendered = images.get(replace(key, animate=False), load=not self._defer_loads)
        if rendered is None:
            return None
        if not rendered.animated:
            return rendered.frame()
        return rendered.frame(_frame_at(rendered, time.monotonic() - self._animation_start))

    def _avatar(self, message: MessageInfo) -> Text:
        """The author's avatar, or an initial tile until it loads (or if it cannot)."""
        url = avatar_url(message.author, message.member, message.guild_id, AVATAR_PIXELS)
        animated = _animated_hash(message.member.avatar if message.member else None) or (
            _animated_hash(message.author.avatar)
        )
        image = self._image(
            message.id, url, AVATAR_COLS, AVATAR_ROWS, exact=True, animated=animated, media=False
        )
        if image is not None:
            return image
        initial = (message.author_name[:1] or "?").upper()
        return Text(f" {initial}  \n    ", style=self.view.avatar_style, no_wrap=True)

    def _inline_emoji(self, message_id: str) -> Callable[[str, str, bool], Text | None]:
        def resolve(emoji_id: str, _name: str, animated: bool) -> Text | None:
            images = self.images
            if images is None or not images.supports_inline:
                return None
            url = emoji_url(emoji_id, animated=animated, pixels=EMOJI_PIXELS)
            return self._image(message_id, url, 2, 1, exact=True, animated=animated)

        return resolve

    def _jumbo(self, message: MessageInfo) -> Text | None:
        """Large emoji for messages made only of custom emoji (like Discord)."""
        content = message.content.strip()
        found = list(CUSTOM_EMOJI.finditer(content))
        if not found or len(found) > JUMBO_LIMIT or CUSTOM_EMOJI.sub("", content).strip():
            return None
        tiles: list[Text] = []
        for match in found:
            animated, emoji_id = match.group(1) == "a", match.group(3)
            url = emoji_url(emoji_id, animated=animated, pixels=EMOJI_PIXELS)
            image = self._image(message.id, url, *JUMBO_EMOJI, exact=True, animated=animated)
            if image is None:
                return None  # fall back to text until every emoji is loaded
            tiles.append(image)
        return _hstack(tiles, JUMBO_EMOJI[1])

    def _media(self, message: MessageInfo, *, hide_gif_link: bool) -> list[RenderableType]:
        """Stickers, attachments and embeds below the message text."""
        glyphs = icons(self.view.ascii_only)
        blocks: list[RenderableType] = []
        for sticker in message.stickers:
            url = sticker_url(sticker, STICKER_PIXELS)
            animated = sticker.format in (StickerFormat.GIF, StickerFormat.APNG)
            image = self._image(message.id, url, *STICKER_CELLS, animated=animated) if url else None
            blocks.append(image or Text(_("[sticker: {name}]").format(name=sticker.name), "dim"))
        for attachment in message.attachments:
            blocks.append(self._attachment(message.id, attachment, glyphs.attachment))
        for embed in message.embeds:
            block = self._embed(message.id, embed, glyphs.embed, hide_gif_link=hide_gif_link)
            if block is not None:
                blocks.append(block)
        return blocks

    def _attachment(self, message_id: str, attachment: Attachment, icon: str) -> RenderableType:
        if attachment.is_image and attachment.width and attachment.height:
            cols, rows = fit(attachment.width, attachment.height, MEDIA_CELLS[0], MEDIA_CELLS[1])
            url = attachment_preview_url(attachment, cols * 10, rows * 20)
            animated = attachment.content_type == "image/gif"
            image = self._image(message_id, url, cols, rows, animated=animated)
            if image is not None:
                return image
        line = Text(f"{icon} ")
        link = f"underline link {attachment.url}" if attachment.url else ""
        line.append(attachment.filename, style=link)
        line.append(f" ({human_size(attachment.size)})", style="dim")
        return line

    def _embed(
        self, message_id: str, embed: Embed, bar: str, *, hide_gif_link: bool
    ) -> RenderableType | None:
        if embed.kind in ("gifv", "image"):
            media = _embed_media(embed)
            if media is not None:
                cols, rows = fit(media.width or 4, media.height or 3, *MEDIA_CELLS)
                url = embed_media_url(media)
                image = self._image(message_id, url, cols, rows, animated=_is_gif(media))
                if image is not None:
                    return image
            if hide_gif_link and embed.url:
                return Text(f"{bar} {embed.url}", style=f"underline link {embed.url}")
            return None
        lines = Text()
        if embed.title:
            title_style = f"bold underline link {embed.url}" if embed.url else "bold"
            lines.append(f"{bar} ", style="dim")
            lines.append(embed.title, style=title_style)
        if embed.description:
            description = render(embed.description, self.context)
            for index, line in enumerate(description.split("\n")):
                if index >= EMBED_LINES:
                    lines.append(f"\n{bar} …", style="dim")
                    break
                if lines:
                    lines.append("\n")
                lines.append(f"{bar} ", style="dim")
                lines.append_text(line)
        media = _embed_media(embed)
        picture = None
        if media is not None:
            cols, rows = fit(media.width or 4, media.height or 3, *EMBED_IMAGE_CELLS)
            url = embed_media_url(media)
            picture = self._image(message_id, url, cols, rows, animated=_is_gif(media))
        if not lines and picture is None:
            return None
        return Group(*(part for part in (lines or None, picture) if part is not None))

    @property
    def _avatar_column(self) -> bool:
        view = self.view
        images = self.images
        return (
            view.density == "cozy" and view.show_avatars and images is not None and images.enabled
        )

    def _track(self, url: str, message_id: str) -> None:
        self._image_owners.setdefault(url, set()).add(message_id)

    # ----- message layout --------------------------------------------------------------------

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

    def _body(self, message: MessageInfo, grouped: bool) -> RenderableType:
        view, glyphs = self.view, icons(self.view.ascii_only)
        text = Text()
        if message.referenced is not None:
            reply = message.referenced
            snippet = plain(reply.content, self.context).replace("\n", " ")[:80]
            text.append(f"{glyphs.reply} @{reply.author_name} {snippet}\n", style="dim")
        jumbo = self._jumbo(message)
        gif_link = any(
            embed.kind == "gifv" and embed.url == message.content.strip()
            for embed in message.embeds
        )
        if jumbo is not None or gif_link:
            body = Text()  # the media below replaces the raw emoji codes or GIF link
        else:
            context = replace(self.context, emoji=self._inline_emoji(message.id))
            body = render(message.content, context)
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
                if body or edited:
                    text.append("\n")
            text.append_text(body)
            if edited:
                text.append(" " + edited, style="dim")
        if message.thread is not None:
            count = message.thread.message_count or 0
            text.append(
                "\n"
                + ngettext(
                    "{icon} {name} · {count} message", "{icon} {name} · {count} messages", count
                ).format(icon=glyphs.thread, name=message.thread.name or "", count=count),
                style="bold",
            )
        if message.failed:
            # A base style: explicit span colours (kitty placeholder image ids) must survive.
            text.style = "red"
            text.append("\n" + _("(failed — press R to retry)"), style="bold red")
        parts: list[RenderableType] = [text] if text else []
        if jumbo is not None:
            parts.append(jumbo)
        parts.extend(self._media(message, hide_gif_link=gif_link))
        if len(parts) == 1:
            return parts[0]
        return Group(*parts) if parts else Text()

    def images_ready(self, urls: set[str]) -> None:
        """Re-render the messages showing any of ``urls``."""
        self._rerender(self._owners(urls))

    def images_evicted(self, urls: set[str]) -> None:
        """``urls`` left the terminal: redraw their messages without reloading off-screen ones.

        Owners fall back to text (or initials) right away; those on screen reload at once and
        the others when they scroll into view.
        """
        owners = self._owners(urls)
        if not owners:
            return
        self._stale |= owners
        self._defer_loads = True
        try:
            self._rerender(owners)
        finally:
            self._defer_loads = False
        self.call_after_refresh(self._reload_visible)

    def _owners(self, urls: set[str]) -> set[str]:
        owners: set[str] = set()
        for url in urls:
            owners |= self._image_owners.get(url, set())
        return owners

    def _visible_message_ids(self) -> set[str]:
        lines = self._lines
        top = int(self.scroll_offset.y)
        visible: set[str] = set()
        for y in range(top, min(top + self.size.height, len(lines))):
            option_id = self.get_option_at_index(lines[y][0]).id
            if option_id is not None and option_id.startswith("m"):
                visible.add(option_id[1:])
        return visible

    def _reload_visible(self) -> None:
        """Reload the images of evicted messages that are on screen now."""
        if not self._stale:
            return
        due = self._stale & self._visible_message_ids()
        if due:
            self._stale -= due
            self._rerender(due)

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if self._stale:
            self.call_after_refresh(self._reload_visible)

    def _rerender(self, message_ids: set[str]) -> None:
        if not message_ids:
            return
        at_bottom = self.is_vertical_scroll_end
        for position, message in enumerate(self.messages):
            if message.id in message_ids:
                self._cache.pop(message.id, None)
                previous = self.messages[position - 1] if position else None
                option = self._option(message, previous)
                self.replace_option_prompt(option.id or "", option.prompt)
        if at_bottom:
            # Loaded images make messages taller; stay at the newest message.
            self.call_after_refresh(self.scroll_end, animate=False)

    # ----- animation -------------------------------------------------------------------------

    def _set_animating(self, message: MessageInfo | None) -> None:
        """Animate the media of the highlighted message only (keeps redraws cheap)."""
        target = None
        view = self.view
        if (
            message is not None
            and view.animate_media
            and view.show_images
            and _has_motion(self._resolved(message))
        ):
            target = message.id
        if target == self._animating:
            return
        previous, self._animating = self._animating, target
        self._animation_start = time.monotonic()
        self._rerender({mid for mid in (previous, target) if mid is not None})
        if target is None:
            if self._animation_timer is not None:
                self._animation_timer.stop()
                self._animation_timer = None
        elif self._animation_timer is None:
            self._animation_timer = self.set_interval(FRAME_SECONDS, self._advance_animation)

    def _advance_animation(self) -> None:
        if self._animating is None:
            return
        self._rerender({self._animating})

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
        self._set_animating(self.highlighted_message())

    def on_unmount(self) -> None:
        if self._animation_timer is not None:
            self._animation_timer.stop()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        message = self.highlighted_message()
        if message is not None and message.thread is not None:
            self.post_message(self.ThreadOpened(message.thread))
