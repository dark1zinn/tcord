from __future__ import annotations

import itertools
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from client.errors import TcordError
from client.models import MessageDeleted, MessageInfo, MessageReference, MessageUpdated
from client.snowflake import time_snowflake
from client.state import (
    ChannelsChanged,
    ConnectionStatusChanged,
    GuildsChanged,
    MembersChanged,
    MessagesChanged,
    StoreChange,
    TypingChanged,
    UnreadChanged,
)
from textual import events, work
from textual.app import ComposeResult
from textual.color import Color, ColorParseError
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static, TextArea, Tree

from ..formatting import channel_title
from ..i18n import _, ngettext
from ..keymap import ChordDispatcher, Match, Pending, Scope, display_sequence, event_tokens
from ..markdown import MarkdownStyles, RenderContext
from ..widgets.channels import ChannelTree
from ..widgets.composer import Composer
from ..widgets.messages import MessageList, ViewOptions
from ..widgets.rail import ServerRail
from ..widgets.status import StatusBar, connection_text
from .confirm import ConfirmScreen

if TYPE_CHECKING:
    from ..app import TcordApp

MAX_MESSAGE_CHARS = 2000
COUNTER_FROM = 1800
TYPING_THROTTLE = 8.0
HISTORY_PAGE = 50
_nonce_counter = itertools.count()


def new_nonce() -> str:
    """Snowflake-shaped nonce; the low bits keep nonces unique within one millisecond."""
    base = int(time_snowflake(datetime.now(UTC)))
    return str(base | (next(_nonce_counter) & 0x3FFFFF))


def _rgb(value: str | None, fallback: str) -> tuple[int, int, int]:
    """A theme colour as an RGB triple (theme variables may be unset)."""
    try:
        return Color.parse(value or fallback).rgb
    except ColorParseError:
        return Color.parse(fallback).rgb


class ChatScreen(Screen):
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (70, "-medium"), (100, "-wide")]  # noqa: RUF012

    def __init__(self) -> None:
        super().__init__()
        self.active_guild: str | None = None
        self.active_channel: str | None = None
        self.reply_to: MessageInfo | None = None
        self.editing: MessageInfo | None = None
        self.show_rail = True
        self.show_sidebar = True
        self._subscribed: set[str] = set()
        self._last_typing: dict[str, float] = {}
        self._typing_timer: Timer | None = None
        self._ack_timer: Timer | None = None
        self.dispatcher = ChordDispatcher({})

    @property
    def tcord(self) -> TcordApp:
        return cast("TcordApp", self.app)

    # ----- composition -----------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield ServerRail(id="rail")
            with Vertical(id="sidebar"):
                yield Static(id="sidebar-header")
                yield ChannelTree(id="channels")
                yield Static(id="user-panel")
            with Vertical(id="main"):
                yield Static(id="channel-header")
                yield MessageList(id="messages")
                yield Static(id="typing")
                yield Static(id="banner")
                yield Composer(self.tcord.keymap, id="composer")
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        self.apply_settings()
        self.reload()
        self.query_one(ChannelTree).focus()

    def apply_settings(self) -> None:
        """Re-read settings and keymap from the app (initial mount and after saving)."""
        settings, keymap = self.tcord.settings, self.tcord.keymap
        self.dispatcher.rebind(keymap.scoped(Scope.SCREEN))
        self.query_one(Composer).keymap = keymap
        self.show_rail = settings.layout.show_server_rail
        self.show_sidebar = settings.layout.show_channel_list
        self.query_one("#sidebar").styles.width = settings.layout.channel_list_width
        self._configure_rail()
        self._show_channels()  # e.g. show_inaccessible_channels changed
        self._apply_layout()
        self._update_mode()
        if self.active_channel is not None:
            self._show_history()

    def _configure_rail(self) -> None:
        theme = self.app.current_theme
        rail = self.query_one(ServerRail)
        rail.configure(
            self.tcord.store,
            self.tcord.images,
            ascii_only=self.tcord.settings.appearance.ascii_only,
            panel=_rgb(theme.panel, "#1e1f22"),
            accent=theme.primary,
            error=theme.error or "#f23f43",
            tile=theme.surface or "#2b2d31",
        )
        if self.is_mounted:
            rail.rebuild(self.active_guild)

    def _apply_layout(self) -> None:
        self.set_class(not self.show_rail, "-hide-rail")
        self.set_class(not self.show_sidebar, "-hide-sidebar")

    def reload(self) -> None:
        """Rebuild everything from the store (initial READY and re-identify)."""
        store = self.tcord.store
        if self.active_guild is not None and self.active_guild not in store.guilds:
            self.active_guild = None
        self.query_one(ServerRail).rebuild(self.active_guild)
        self.query_one(ChannelTree).reset_forums()  # READY dropped the fetched posts
        self._show_channels()
        me = store.me
        self.query_one("#user-panel", Static).update(me.display_name if me else "")
        self.query_one(StatusBar).connection = connection_text(store.connection, None)
        if self.active_channel is not None and self.active_channel in store.channels:
            self.open_channel(self.active_channel)

    def _show_channels(self) -> None:
        store = self.tcord.store
        tree = self.query_one(ChannelTree)
        tree.show(
            store,
            self.active_guild,
            self.tcord.settings.appearance.ascii_only,
            show_inaccessible=self.tcord.settings.layout.show_inaccessible_channels,
        )
        if self.active_guild is None:
            header = _("Direct Messages")
        else:
            header = store.guilds[self.active_guild].name
        self.query_one("#sidebar-header", Static).update(header)
        if self.active_channel is not None:
            tree.highlight_channel(self.active_channel)

    # ----- store changes ---------------------------------------------------------------------

    def apply_changes(self, changes: list[StoreChange]) -> None:
        rail = self.query_one(ServerRail)
        active_messages: list[MessagesChanged] = []
        for change in changes:
            match change:
                case GuildsChanged():
                    rail.rebuild(self.active_guild)
                case ChannelsChanged(guild_id):
                    if guild_id == self.active_guild or guild_id is None:
                        self._show_channels()
                    rail.refresh_badges()  # visibility or unread state may have moved
                case MessagesChanged(channel_id):
                    if channel_id == self.active_channel:
                        active_messages.append(change)
                case UnreadChanged(channel_ids):
                    self.query_one(ChannelTree).refresh_badges(channel_ids)
                    rail.refresh_badges()
                case TypingChanged(channel_id):
                    if channel_id == self.active_channel:
                        self._refresh_typing()
                case ConnectionStatusChanged(status, retry_in):
                    self.query_one(StatusBar).connection = connection_text(status, retry_in)
                case MembersChanged(guild_id):
                    if self.active_channel is not None and guild_id == self._channel_guild():
                        self._show_history(keep_position=True)
        if active_messages:
            self._messages_changed(active_messages)

    def _messages_changed(self, changes: list[MessagesChanged]) -> None:
        """Apply one batch: removals/resets rebuild once, otherwise patch in place."""
        if any(change.op in ("remove", "reset") for change in changes):
            self._show_history(keep_position=True)
            return
        messages = self.query_one(MessageList)
        history = self.tcord.store.history_state(self.active_channel or "")
        if history is None:
            return
        by_id = {message.id: message for message in history.messages}
        shown = {message.id for message in messages.messages}
        for change in changes:
            message = by_id.get(change.message_id or "")
            if message is None:
                continue
            if message.id in shown:
                messages.update(message)
            elif messages.append(message):
                shown.add(message.id)
                self._schedule_ack()
            else:
                shown.add(message.id)
                self.query_one(StatusBar).new_messages = True
        messages.trim(len(history.messages))

    # ----- channels and history --------------------------------------------------------------

    def choose_guild(self, guild_id: str | None) -> None:
        self.active_guild = guild_id
        if guild_id is not None and guild_id not in self._subscribed:
            self._subscribed.add(guild_id)
            self.tcord.client.gateway.subscribe_guild(guild_id)
        self.query_one(ServerRail).set_active(guild_id)
        self._show_channels()
        self.query_one(ChannelTree).focus()

    def images_ready(self, urls: set[str]) -> None:
        """Redraw whatever shows one of the freshly loaded images."""
        self.query_one(ServerRail).images_ready(urls)
        self.query_one(MessageList).images_ready(urls)

    def open_channel(self, channel_id: str) -> None:
        store = self.tcord.store
        channel = store.channels.get(channel_id)
        if channel is None or not channel.is_readable:
            return
        if channel_id != self.active_channel:
            self._clear_pending_action()
        self.active_channel = channel_id
        store.set_active(channel_id)
        title = channel_title(channel, store.me)
        header = title if not channel.topic else f"{title}  ·  {channel.topic}"
        self.query_one("#channel-header", Static).update(header)
        self.query_one(Composer).placeholder = _("Message {target}").format(target=title)
        self.query_one(ChannelTree).highlight_channel(channel_id)
        self.query_one(StatusBar).new_messages = False
        history = store.history_state(channel_id)
        self._show_history()
        if history is None or not history.loaded:
            self._fetch_history(channel_id)
        self._refresh_typing()

    def jump_to(self, guild_id: str | None, channel_id: str | None) -> None:
        """Switcher target: show ``guild_id`` (None = DMs) and open ``channel_id`` if given."""
        if guild_id != self.active_guild:
            self.choose_guild(guild_id)
        if channel_id is not None:
            self.open_channel(channel_id)
            self.query_one(Composer).focus()

    def _render_context(self) -> RenderContext:
        store, settings = self.tcord.store, self.tcord.settings

        def user_name(user_id: str) -> str | None:
            user = store.users.get(user_id)
            return user.display_name if user else None

        def channel_name(channel_id: str) -> str | None:
            channel = store.channels.get(channel_id)
            return channel.name if channel else None

        def role(role_id: str) -> tuple[str, int] | None:
            for guild in store.guilds.values():
                for item in guild.roles:
                    if item.id == role_id:
                        return item.name, item.color
            return None

        cached = getattr(self, "_markdown_context", None)
        key = (id(store), settings.appearance)
        if cached is not None and cached[0] == key:
            return cached[1]
        theme = self.app.current_theme
        panel = theme.panel or theme.background or "#1e1f22"
        styles = MarkdownStyles(
            code=f"on {panel}",
            link=f"underline {theme.primary}",
            mention=f"bold {theme.primary}",
        )
        context = RenderContext(
            user_name=user_name,
            channel_name=channel_name,
            role=role,
            ascii_only=settings.appearance.ascii_only,
            time_format=settings.appearance.time_format,
            styles=styles,
        )
        self._markdown_context = (key, context)
        return context

    def _view_options(self) -> ViewOptions:
        appearance = self.tcord.settings.appearance
        theme = self.app.current_theme
        background = Color.parse(theme.background or "#313338")
        mention = background.blend(Color.parse(theme.warning or "#f0b232"), 0.18)
        me = self.tcord.store.me
        return ViewOptions(
            density=appearance.message_density,
            show_timestamps=appearance.show_timestamps,
            time_format=appearance.time_format,
            ascii_only=appearance.ascii_only,
            me_id=me.id if me else None,
            mention_style=f"on {mention.hex}",
            show_avatars=appearance.show_avatars,
            show_images=appearance.show_images,
            animate_media=appearance.animate_media and not appearance.reduced_motion,
            background=background.rgb,
            avatar_style=f"bold on {theme.panel or '#1e1f22'}",
            image_protocol=self.tcord.images.protocol,
        )

    def _show_history(self, *, keep_position: bool = False) -> None:
        if self.active_channel is None:
            return
        store = self.tcord.store
        channel = store.channels.get(self.active_channel)
        label = channel_title(channel, store.me) if channel else ""
        messages = self.query_one(MessageList)
        messages.store, messages.images = store, self.tcord.images
        history = store.history_state(self.active_channel)
        messages.show(
            history,
            label,
            self._view_options(),
            self._render_context(),
            keep_position=keep_position,
        )
        if history is not None:
            self._request_authors(history.messages)
        if not keep_position:
            self._schedule_ack()

    def _request_authors(self, messages: Iterable[MessageInfo]) -> None:
        """Ask for members missing from the cache (their nicknames and role colours)."""
        guild_id = self._channel_guild()
        if guild_id is None:
            return
        authors = [message.author.id for message in messages if not message.pending]
        missing = self.tcord.store.missing_members(guild_id, authors)
        if missing:
            self.tcord.client.gateway.request_members(guild_id, missing)

    def _channel_guild(self) -> str | None:
        """Guild of the open channel (the sidebar may be showing another guild)."""
        channel = self.tcord.store.channels.get(self.active_channel or "")
        return channel.guild_id if channel is not None else None

    @work(exclusive=True, group="history", exit_on_error=False)
    async def _fetch_history(self, channel_id: str) -> None:
        page = await self.tcord.guard(self.tcord.client.messages.history(channel_id, None, 50))
        if page is None:
            return
        self.tcord.store.set_history(channel_id, page, older=False, limit=HISTORY_PAGE)
        if channel_id == self.active_channel:
            self._show_history()

    def on_message_list_load_older(self, event: MessageList.LoadOlder) -> None:
        event.stop()
        if self.active_channel is not None:
            self._fetch_older(self.active_channel)

    @work(exclusive=True, group="older", exit_on_error=False)
    async def _fetch_older(self, channel_id: str) -> None:
        history = self.tcord.store.history_state(channel_id)
        if history is None or not history.has_more_before or not history.messages:
            return
        oldest = next((m.id for m in history.messages if not m.pending), None)
        if oldest is None:
            return
        page = await self.tcord.guard(
            self.tcord.client.messages.history(channel_id, oldest, HISTORY_PAGE)
        )
        if page is None:
            return
        self.tcord.store.set_history(channel_id, page, older=True, limit=HISTORY_PAGE)
        if channel_id == self.active_channel:
            self._show_history(keep_position=True)

    # ----- read acks and typing --------------------------------------------------------------

    def _schedule_ack(self) -> None:
        if self._ack_timer is not None:
            self._ack_timer.stop()
        self._ack_timer = self.set_timer(1.0, self._ack_if_visible)

    def _ack_if_visible(self) -> None:
        channel_id = self.active_channel
        store = self.tcord.store
        if channel_id is None or not self.app.app_focus:
            return
        messages = self.query_one(MessageList)
        if not messages.last_visible_is_latest():
            return
        latest = next((m for m in reversed(messages.messages) if not m.pending), None)
        if latest is None:
            return
        if not store.is_unread(channel_id) and not store.mention_count(channel_id):
            return
        self.apply_changes(store.mark_read(channel_id, latest.id))
        self.run_worker(
            self.tcord.guard(self.tcord.client.messages.ack(channel_id, latest.id), quiet=True),
            group="ack",
        )

    def _refresh_typing(self) -> None:
        widget = self.query_one("#typing", Static)
        users = self.tcord.store.typing_users(self.active_channel) if self.active_channel else []
        names = [user.display_name for user in users]
        if not names:
            text = ""
        elif len(names) == 1:
            text = _("{a} is typing…").format(a=names[0])
        elif len(names) == 2:
            text = _("{a} and {b} are typing…").format(a=names[0], b=names[1])
        else:
            text = ngettext(
                "{count} person is typing…", "Several people are typing…", len(names)
            ).format(count=len(names))
        widget.update(text)
        if names and self._typing_timer is None:
            self._typing_timer = self.set_interval(1.0, self._refresh_typing)
        elif not names and self._typing_timer is not None:
            self._typing_timer.stop()
            self._typing_timer = None

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if not isinstance(event.text_area, Composer):
            return
        length = len(event.text_area.text)
        self._update_banner(length)
        channel_id = self.active_channel
        if channel_id is None or self.editing is not None or not event.text_area.text.strip():
            return
        now = time.monotonic()
        if now - self._last_typing.get(channel_id, -TYPING_THROTTLE) >= TYPING_THROTTLE:
            self._last_typing[channel_id] = now
            self.run_worker(
                self.tcord.guard(self.tcord.client.messages.trigger_typing(channel_id), quiet=True),
                group="typing",
            )

    # ----- composer --------------------------------------------------------------------------

    def _update_banner(self, length: int | None = None) -> None:
        banner = self.query_one("#banner", Static)
        parts = []
        if self.editing is not None:
            parts.append(_("Editing message — Escape to cancel"))
        elif self.reply_to is not None:
            parts.append(
                _("Replying to {name} — Escape to cancel").format(name=self.reply_to.author_name)
            )
        if length is None:
            length = len(self.query_one(Composer).text)
        if length > COUNTER_FROM:
            parts.append(f"{length}/{MAX_MESSAGE_CHARS}")
        banner.update("  ".join(parts))
        banner.set_class(not parts, "-empty")
        banner.set_class(length > MAX_MESSAGE_CHARS, "-error")

    def _clear_pending_action(self) -> None:
        was_editing = self.editing is not None
        self.reply_to = None
        self.editing = None
        if was_editing:
            self.query_one(Composer).clear()
        self._update_banner()

    def on_composer_submitted(self, event: Composer.Submitted) -> None:
        event.stop()
        channel_id = self.active_channel
        if channel_id is None:
            self.notify(_("Open a channel first."), severity="warning")
            return
        text = event.text.strip()
        if len(event.text) > MAX_MESSAGE_CHARS:
            self.notify(
                _("Messages can be at most {limit} characters.").format(limit=MAX_MESSAGE_CHARS),
                severity="error",
            )
            return
        composer = self.query_one(Composer)
        if self.editing is not None:
            target = self.editing
            if not text:
                self._confirm_delete(target)
                return
            self._clear_pending_action()
            composer.clear()
            if text != target.content:
                self._edit(target, text)
            return
        if not text:
            return
        me = self.tcord.store.me
        if me is None:
            return
        nonce = new_nonce()
        reply = self.reply_to
        pending = MessageInfo(
            id=nonce,
            channel_id=channel_id,
            author=me,
            content=event.text,
            timestamp=datetime.now(UTC),
            guild_id=self.active_guild,
            nonce=nonce,
            referenced=reply,
        )
        self._clear_pending_action()
        composer.clear()
        self._send(pending)

    def _send(self, pending: MessageInfo) -> None:
        store = self.tcord.store
        nonce = pending.nonce
        assert nonce is not None
        self.apply_changes(store.add_pending(pending))
        reply = pending.referenced
        reference = MessageReference(reply.id, reply.channel_id, reply.guild_id) if reply else None

        async def deliver() -> None:
            sent = await self.tcord.guard(
                self.tcord.client.messages.send(
                    pending.channel_id, pending.content, reply_to=reference, nonce=nonce
                )
            )
            if sent is None:
                self.apply_changes(store.fail_pending(nonce))
            else:
                self.apply_changes(store.resolve_pending(nonce, sent))

        self.run_worker(deliver(), group="send")

    def _edit(self, target: MessageInfo, content: str) -> None:
        async def apply() -> None:
            edited = await self.tcord.guard(
                self.tcord.client.messages.edit(target.channel_id, target.id, content)
            )
            if edited is not None:
                self.apply_changes(self.tcord.store.apply(MessageUpdated(edited)))

        self.run_worker(apply(), group="edit")

    def _confirm_delete(self, target: MessageInfo) -> None:
        async def run() -> None:
            confirmed = await self.app.push_screen_wait(
                ConfirmScreen(_("Delete this message?"), _("Delete"))
            )
            if not confirmed:
                return
            if self.editing is not None and self.editing.id == target.id:
                self._clear_pending_action()
            try:
                await self.tcord.client.messages.delete(target.channel_id, target.id)
            except TcordError as error:
                self.tcord.report(error)
                return
            self.apply_changes(
                self.tcord.store.apply(
                    MessageDeleted(target.id, target.channel_id, target.guild_id)
                )
            )

        self.run_worker(run(), group="delete")

    def on_composer_cancelled(self, event: Composer.Cancelled) -> None:
        event.stop()
        if self.reply_to is not None or self.editing is not None:
            self._clear_pending_action()
        elif self.tcord.keymap.modal:
            self.query_one(MessageList).focus()

    def on_composer_edit_last(self, event: Composer.EditLast) -> None:
        event.stop()
        me = self.tcord.store.me
        messages = self.query_one(MessageList).messages
        own = next(
            (m for m in reversed(messages) if me and m.author.id == me.id and not m.pending),
            None,
        )
        if own is not None:
            self._start_edit(own)

    def _start_edit(self, message: MessageInfo) -> None:
        self.reply_to = None
        self.editing = message
        composer = self.query_one(Composer)
        composer.load_text(message.content)
        composer.move_cursor(composer.document.end)
        composer.focus()
        self._update_banner()

    # ----- widget events ---------------------------------------------------------------------

    def on_server_rail_guild_chosen(self, event: ServerRail.GuildChosen) -> None:
        event.stop()
        self.choose_guild(event.guild_id)

    def on_channel_tree_channel_chosen(self, event: ChannelTree.ChannelChosen) -> None:
        event.stop()
        self.open_channel(event.channel_id)

    def on_channel_tree_forum_expanded(self, event: ChannelTree.ForumExpanded) -> None:
        event.stop()
        self._load_posts(event.channel_id, event.offset)

    @work(group="threads", exit_on_error=False)
    async def _load_posts(self, forum_id: str, offset: int) -> None:
        tree = self.query_one(ChannelTree)
        gateway = self.tcord.client.gateway
        page = await self.tcord.guard(gateway.threads(forum_id, offset=offset))
        if page is None:
            tree.forum_failed(forum_id)
            return
        tree.forum_loaded(forum_id, len(page.threads), has_more=page.has_more)
        changes = self.tcord.store.set_threads(page.threads)
        # The "load more" row changes even when no post is new.
        self.apply_changes(changes or [ChannelsChanged(self.active_guild)])

    def on_message_list_thread_opened(self, event: MessageList.ThreadOpened) -> None:
        event.stop()
        store = self.tcord.store
        if event.thread.id not in store.channels:
            self.apply_changes(store.set_threads([event.thread]))  # list it in the tree too
        self.open_channel(event.thread.id)
        self.query_one(Composer).focus()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self._update_mode()
        if isinstance(event.widget, MessageList):
            self._schedule_ack()

    def _update_mode(self) -> None:
        status = self.query_one(StatusBar)
        if not self.tcord.keymap.modal:
            status.mode = ""
        elif isinstance(self.focused, Composer):
            status.mode = _("INSERT")
        else:
            status.mode = _("NORMAL")

    # ----- keyboard --------------------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        focused = self.focused
        if isinstance(focused, Composer):
            return
        status = self.query_one(StatusBar)
        tokens = event_tokens(event.key, event.character)
        result = self.dispatcher.feed(tokens)
        if isinstance(result, Match):
            self._claim(event)
            status.chord = ""
            self.run_action_id(result.action)
            return
        if isinstance(result, Pending):
            self._claim(event)
            status.chord = display_sequence(result.prefix)
            return
        status.chord = ""
        printable = event.character is not None and event.is_printable
        if not printable:
            return
        if self.tcord.keymap.modal:
            self._claim(event)  # NORMAL mode: unbound characters do nothing
        elif isinstance(focused, MessageList) and event.character is not None:
            self._claim(event)
            composer = self.query_one(Composer)
            composer.focus()
            composer.insert(event.character)

    @staticmethod
    def _claim(event: events.Key) -> None:
        event.stop()
        event.prevent_default()

    def run_action_id(self, action: str) -> None:
        handler = self._handlers().get(action)
        if handler is not None:
            handler()

    def _handlers(self) -> dict[str, Callable[[], object]]:
        app = self.tcord
        return {
            "app.quit": app.exit,
            "app.switcher": app.action_open_switcher,
            "app.palette": app.action_command_palette,
            "app.help": app.action_open_help,
            "app.settings": app.action_open_settings,
            "toggle.rail": self.action_toggle_rail,
            "toggle.channels": self.action_toggle_channels,
            "focus.rail": lambda: self._focus(ServerRail),
            "focus.channels": lambda: self._focus(ChannelTree),
            "focus.messages": lambda: self._focus(MessageList),
            "focus.composer": lambda: self._focus(Composer),
            "focus.left": lambda: self._focus_step(-1),
            "focus.right": lambda: self._focus_step(1),
            "cursor.down": lambda: self._call_focused("action_cursor_down"),
            "cursor.up": lambda: self._call_focused("action_cursor_up"),
            "cursor.top": self._cursor_top,
            "cursor.bottom": self._cursor_bottom,
            "page.down": lambda: self._call_focused("action_page_down"),
            "page.up": lambda: self._call_focused("action_page_up"),
            "item.open": self._item_open,
            "item.back": lambda: self._focus_step(-1),
            "guild.next": lambda: self._step_guild(1),
            "guild.prev": lambda: self._step_guild(-1),
            "channel.next": lambda: self._step_channel(1, unread=False),
            "channel.prev": lambda: self._step_channel(-1, unread=False),
            "channel.next_unread": lambda: self._step_channel(1, unread=True),
            "channel.prev_unread": lambda: self._step_channel(-1, unread=True),
            "message.reply": self.action_reply,
            "message.edit": self.action_edit,
            "message.delete": self.action_delete,
            "message.retry": self.action_retry,
        }

    def _panes(self) -> list[Widget]:
        panes: list[Widget] = []
        if self.query_one(ServerRail).display and self.show_rail:
            panes.append(self.query_one(ServerRail))
        if self.show_sidebar:
            panes.append(self.query_one(ChannelTree))
        panes += [self.query_one(MessageList), self.query_one(Composer)]
        return panes

    def _focus(self, kind: type[Widget]) -> None:
        if kind is ChannelTree and not self.show_sidebar:
            self.action_toggle_channels()
        if kind is ServerRail and not self.show_rail:
            self.action_toggle_rail()
        self.query_one(kind).focus()

    def _focus_step(self, step: int) -> None:
        panes = self._panes()
        focused = self.focused
        index = panes.index(focused) if focused in panes else len(panes) - 2
        panes[max(0, min(len(panes) - 1, index + step))].focus()

    def _call_focused(self, action: str) -> None:
        method = getattr(self.focused, action, None)
        if callable(method):
            method()

    def _cursor_top(self) -> None:
        focused = self.focused
        if isinstance(focused, Tree):
            focused.move_cursor_to_line(0)
        else:
            self._call_focused("action_first")

    def _cursor_bottom(self) -> None:
        focused = self.focused
        if isinstance(focused, Tree):
            focused.move_cursor_to_line(focused.last_line)
        else:
            self._call_focused("action_last")

    def _item_open(self) -> None:
        focused = self.focused
        if isinstance(focused, ServerRail) and focused.highlighted is not None:
            self.choose_guild(focused.guild_at(focused.highlighted))
        elif isinstance(focused, ChannelTree):
            focused.action_select_cursor()
        elif isinstance(focused, MessageList):
            focused.action_select()  # opens the thread a message started

    def _step_guild(self, step: int) -> None:
        rail = self.query_one(ServerRail)
        index = (rail.index_of(self.active_guild) + step) % max(1, rail.count)
        self.choose_guild(rail.guild_at(index))

    def _step_channel(self, step: int, *, unread: bool) -> None:
        visible = self.query_one(ChannelTree).visible_channels()
        if not visible:
            return
        store = self.tcord.store
        start = visible.index(self.active_channel) if self.active_channel in visible else -step
        for offset in range(1, len(visible) + 1):
            candidate = visible[(start + step * offset) % len(visible)]
            if not unread or store.is_unread(candidate):
                self.open_channel(candidate)
                return
        if unread:
            self.notify(_("No unread channels here."))

    def action_toggle_rail(self) -> None:
        self.show_rail = not self.show_rail
        self._apply_layout()

    def action_toggle_channels(self) -> None:
        if self.has_class("-narrow"):
            # Too narrow for a side pane: show the channel list as an overlay instead.
            self.toggle_class("-sidebar-overlay")
            if self.has_class("-sidebar-overlay"):
                self.query_one(ChannelTree).focus()
            return
        self.show_sidebar = not self.show_sidebar
        self._apply_layout()

    # ----- message actions -------------------------------------------------------------------

    def _target(self) -> MessageInfo | None:
        message = self.query_one(MessageList).highlighted_message()
        if message is None:
            self.notify(_("Highlight a message first."), severity="warning")
        return message

    def _own(self, message: MessageInfo) -> bool:
        me = self.tcord.store.me
        return me is not None and message.author.id == me.id

    def action_reply(self) -> None:
        message = self._target()
        if message is None or message.pending:
            return
        self.editing = None
        self.reply_to = message
        self.query_one(Composer).focus()
        self._update_banner()

    def action_edit(self) -> None:
        message = self._target()
        if message is None or message.pending:
            return
        if not self._own(message):
            self.notify(_("You can only edit your own messages"), severity="warning")
            return
        self._start_edit(message)

    def action_delete(self) -> None:
        message = self._target()
        if message is None:
            return
        if not self._own(message):
            self.notify(_("You can only delete your own messages"), severity="warning")
            return
        if message.pending and message.failed and message.nonce:
            self.tcord.store.pending.pop(message.nonce, None)
            self.apply_changes(
                self.tcord.store.apply(MessageDeleted(message.id, message.channel_id, None))
            )
            return
        self._confirm_delete(message)

    def action_retry(self) -> None:
        message = self._target()
        if message is None or not message.failed or message.nonce is None:
            return
        self._send(replace(message, failed=False))
