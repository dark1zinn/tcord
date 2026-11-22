"""Channel list: categories, channels with their active threads, forum posts and DMs."""

from __future__ import annotations

from client.models import Channel, ChannelKind
from client.state import ChatStore
from rich.text import Text
from textual.message import Message
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from ..formatting import channel_prefix, dm_name, icons
from ..i18n import _

#: Shown in the tree; threads appear under their parent instead of on their own.
_LISTED = {
    ChannelKind.TEXT,
    ChannelKind.ANNOUNCEMENT,
    ChannelKind.VOICE,
    ChannelKind.STAGE,
    ChannelKind.FORUM,
    ChannelKind.MEDIA,
}


#: Node data of a forum's "load more posts" row: this prefix plus the forum id.
_MORE = "more:"


def _order(channel: Channel) -> tuple[int, int]:
    return channel.position, int(channel.id) if channel.id.isdigit() else 0


class ChannelTree(Tree[str]):
    """Channels of the active guild (by category) or the DM list. Node data = channel id.

    Channels the account cannot view are left out unless ``show_inaccessible`` is set, in
    which case they are listed dimmed with a lock. Text channels show their active threads
    as children; forum channels load their posts when expanded.
    """

    class ChannelChosen(Message):
        def __init__(self, channel_id: str) -> None:
            super().__init__()
            self.channel_id = channel_id

    class ForumExpanded(Message):
        """Fetch the forum's posts starting at ``offset`` (expanded or "load more")."""

        def __init__(self, channel_id: str, offset: int) -> None:
            super().__init__()
            self.channel_id = channel_id
            self.offset = offset

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id)
        self.show_root = False
        self.guide_depth = 2
        self.auto_expand = False  # Enter opens a channel; Space toggles its threads.
        self._channel_nodes: dict[str, TreeNode[str]] = {}
        self._store: ChatStore | None = None
        self._ascii = False
        self._show_inaccessible = False
        self._expanded_forums: set[str] = set()
        #: Forum id -> posts fetched so far (present once the first page arrived).
        self._forum_offsets: dict[str, int] = {}
        self._forum_loading: set[str] = set()
        self._forum_more: set[str] = set()

    # ----- building --------------------------------------------------------------------------

    def show(
        self,
        store: ChatStore,
        guild_id: str | None,
        ascii_only: bool,
        *,
        show_inaccessible: bool = False,
    ) -> None:
        cursor = self.cursor_node.data if self.cursor_node is not None else None
        self._store, self._ascii = store, ascii_only
        self._show_inaccessible = show_inaccessible
        self.clear()
        self._channel_nodes = {}
        if guild_id is None:
            for channel in store.private_channels():
                self._leaf(self.root, channel)
        else:
            self._fill_guild(store, guild_id)
        self.root.expand()
        if cursor is not None and cursor in self._channel_nodes:
            self.move_cursor(self._channel_nodes[cursor])

    def _visible(self, channel: Channel) -> bool:
        store = self._store
        assert store is not None
        return self._show_inaccessible or store.can_view(channel.id)

    def _fill_guild(self, store: ChatStore, guild_id: str) -> None:
        channels = [
            channel
            for channel in store.guild_channels(guild_id)
            if (channel.kind in _LISTED or channel.is_category) and self._visible(channel)
        ]
        categories = sorted((c for c in channels if c.is_category), key=_order)
        listed = [c for c in channels if not c.is_category]
        known_categories = {category.id for category in categories}
        loose = sorted((c for c in listed if c.parent_id not in known_categories), key=_order)
        for channel in loose:
            self._add_channel(self.root, channel)
        for category in categories:
            children = sorted((c for c in listed if c.parent_id == category.id), key=_order)
            if not children:
                continue  # like Discord: no visible channels, no category
            branch = self.root.add(
                Text((category.name or "").upper(), style="bold dim"), expand=True
            )
            for channel in children:
                self._add_channel(branch, channel)

    def _add_channel(self, parent: TreeNode[str], channel: Channel) -> None:
        store = self._store
        assert store is not None
        if channel.is_forum:
            node = parent.add(self._label(channel), data=channel.id, allow_expand=True)
            self._channel_nodes[channel.id] = node
            for post in store.thread_children(channel.id):
                self._leaf(node, post)
            if channel.id in self._forum_more:
                more = Text("  " + _("Load more posts…"), style="dim italic")
                node.add_leaf(more, data=_MORE + channel.id)
            if channel.id in self._expanded_forums:
                node.expand()
            return
        threads = store.thread_children(channel.id) if channel.is_readable else []
        if not threads:
            self._leaf(parent, channel)
            return
        # Always open and without a toggle arrow, so the name lines up with its siblings.
        node = parent.add(self._label(channel), data=channel.id, expand=True, allow_expand=False)
        self._channel_nodes[channel.id] = node
        for thread in threads:
            self._leaf(node, thread)

    def _leaf(self, parent: TreeNode[str], channel: Channel) -> None:
        node = parent.add_leaf(self._label(channel), data=channel.id)
        self._channel_nodes[channel.id] = node

    # ----- labels ----------------------------------------------------------------------------

    def _label(self, channel: Channel) -> Text:
        store = self._store
        assert store is not None
        glyphs = icons(self._ascii)
        unread = store.is_unread(channel.id) and (not channel.is_thread or channel.joined)
        mentions = store.mention_count(channel.id)
        accessible = channel.is_private or store.can_view(channel.id)
        label = Text()
        label.append(glyphs.unread + " " if unread else "  ", style="bold")
        if channel.is_private:
            label.append(dm_name(channel, store.me), style="bold" if unread else "")
        elif channel.is_thread:
            style = "bold" if unread else ("dim" if channel.is_archived else "")
            label.append(f"{glyphs.thread} {channel.name or ''}", style=style)
        elif channel.is_forum:
            label.append(f"{glyphs.forum} {channel.name or ''}", style="bold" if unread else "")
        else:
            style = "dim" if channel.is_voice else ("bold" if unread else "")
            label.append(channel_prefix(channel, self._ascii) + (channel.name or ""), style=style)
        if not accessible:
            label.stylize("dim")
            label.append(f" {glyphs.locked}", style="dim")
        if mentions:
            label.append(f" ({mentions})", style="bold")
        return label

    def refresh_badges(self, channel_ids: tuple[str, ...] | None = None) -> None:
        store = self._store
        if store is None:
            return
        for channel_id in channel_ids if channel_ids is not None else tuple(self._channel_nodes):
            node = self._channel_nodes.get(channel_id)
            channel = store.channels.get(channel_id)
            if node is not None and channel is not None:
                node.set_label(self._label(channel))

    # ----- navigation ------------------------------------------------------------------------

    def visible_channels(self) -> list[str]:
        """Readable, accessible channel and thread ids in display order."""
        store = self._store
        if store is None:
            return []
        ordered: list[str] = []

        def walk(node: TreeNode[str]) -> None:
            for child in node.children:
                channel = store.channels.get(child.data or "")
                if channel is not None and channel.is_readable and store.can_view(channel.id):
                    ordered.append(channel.id)
                walk(child)

        walk(self.root)
        return ordered

    def highlight_channel(self, channel_id: str) -> None:
        node = self._channel_nodes.get(channel_id)
        if node is not None:
            parent = node.parent
            while parent is not None:
                parent.expand()
                parent = parent.parent
            self.move_cursor(node)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded[str]) -> None:
        store = self._store
        channel = store.channels.get(event.node.data or "") if store else None
        if channel is None or not channel.is_forum:
            return
        event.stop()
        self._expanded_forums.add(channel.id)
        if channel.id not in self._forum_offsets:
            self._request_posts(channel.id)

    def on_tree_node_collapsed(self, event: Tree.NodeCollapsed[str]) -> None:
        self._expanded_forums.discard(event.node.data or "")

    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        event.stop()
        channel_id = event.node.data
        store = self._store
        if channel_id is None or store is None:
            return
        if channel_id.startswith(_MORE):
            self._request_posts(channel_id.removeprefix(_MORE))
            return
        channel = store.channels.get(channel_id)
        if channel is None:
            return
        if not channel.is_private and not store.can_view(channel_id):
            self.notify(_("You don't have access to this channel."), severity="warning")
        elif channel.is_forum:
            event.node.toggle()
        elif channel.is_voice:
            self.notify(_("Voice channels aren't supported yet."))
        elif channel.is_readable:
            self.post_message(self.ChannelChosen(channel_id))

    # ----- forum posts -----------------------------------------------------------------------

    def _request_posts(self, forum_id: str) -> None:
        if forum_id in self._forum_loading:
            return
        self._forum_loading.add(forum_id)
        self.post_message(self.ForumExpanded(forum_id, self._forum_offsets.get(forum_id, 0)))

    def forum_loaded(self, forum_id: str, count: int, *, has_more: bool) -> None:
        """A page of ``count`` posts arrived; the next request continues after them."""
        self._forum_loading.discard(forum_id)
        self._forum_offsets[forum_id] = self._forum_offsets.get(forum_id, 0) + count
        if has_more:
            self._forum_more.add(forum_id)
        else:
            self._forum_more.discard(forum_id)

    def forum_failed(self, forum_id: str) -> None:
        """The request failed: expanding again (or "load more") retries it."""
        self._forum_loading.discard(forum_id)
        if self._forum_offsets.get(forum_id) is None:
            self._expanded_forums.discard(forum_id)
            node = self._channel_nodes.get(forum_id)
            if node is not None:
                node.collapse()

    def reset_forums(self) -> None:
        """Forget fetched forum pages (the store lost the posts, e.g. after READY)."""
        self._forum_offsets.clear()
        self._forum_more.clear()
        self._forum_loading.clear()
        self._expanded_forums.clear()
