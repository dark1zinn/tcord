from __future__ import annotations

from client.models import Channel, ChannelKind
from client.state import ChatStore
from rich.text import Text
from textual.message import Message
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from ..formatting import channel_prefix, dm_name, icons
from ..i18n import _

_HIDDEN = {
    ChannelKind.FORUM,
    ChannelKind.MEDIA,
    ChannelKind.ANNOUNCEMENT_THREAD,
    ChannelKind.PUBLIC_THREAD,
    ChannelKind.PRIVATE_THREAD,
}


def _order(channel: Channel) -> tuple[int, int]:
    return channel.position, int(channel.id) if channel.id.isdigit() else 0


class ChannelTree(Tree[str]):
    """Channels of the active guild (by category) or the DM list. Node data = channel id."""

    class ChannelChosen(Message):
        def __init__(self, channel_id: str) -> None:
            super().__init__()
            self.channel_id = channel_id

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id)
        self.show_root = False
        self.guide_depth = 2
        self._channel_nodes: dict[str, TreeNode[str]] = {}
        self._store: ChatStore | None = None
        self._ascii = False

    def show(self, store: ChatStore, guild_id: str | None, ascii_only: bool) -> None:
        self._store, self._ascii = store, ascii_only
        self.clear()
        self._channel_nodes = {}
        if guild_id is None:
            for channel in store.private_channels():
                self._leaf(self.root, channel)
        else:
            self._fill_guild(store, guild_id)
        self.root.expand()

    def _fill_guild(self, store: ChatStore, guild_id: str) -> None:
        channels = [
            channel for channel in store.guild_channels(guild_id) if channel.kind not in _HIDDEN
        ]
        categories = sorted((c for c in channels if c.is_category), key=_order)
        loose = sorted(
            (c for c in channels if not c.is_category and c.parent_id is None), key=_order
        )
        for channel in loose:
            self._leaf(self.root, channel)
        for category in categories:
            branch = self.root.add(
                Text((category.name or "").upper(), style="bold dim"), expand=True
            )
            children = sorted(
                (c for c in channels if c.parent_id == category.id and not c.is_category),
                key=_order,
            )
            for channel in children:
                self._leaf(branch, channel)

    def _leaf(self, parent: TreeNode[str], channel: Channel) -> None:
        node = parent.add_leaf(self._label(channel), data=channel.id)
        self._channel_nodes[channel.id] = node

    def _label(self, channel: Channel) -> Text:
        store = self._store
        assert store is not None
        unread = store.is_unread(channel.id)
        mentions = store.mention_count(channel.id)
        label = Text()
        label.append(icons(self._ascii).unread + " " if unread else "  ", style="bold")
        if channel.is_private:
            label.append(dm_name(channel, store.me), style="bold" if unread else "")
        else:
            style = "dim" if channel.is_voice else ("bold" if unread else "")
            label.append(channel_prefix(channel, self._ascii) + (channel.name or ""), style=style)
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

    def visible_channels(self) -> list[str]:
        """Readable channel ids in display order."""
        store = self._store
        if store is None:
            return []
        return [
            channel_id
            for channel_id in self._channel_nodes
            if (channel := store.channels.get(channel_id)) is not None and channel.is_readable
        ]

    def highlight_channel(self, channel_id: str) -> None:
        node = self._channel_nodes.get(channel_id)
        if node is not None:
            if node.parent is not None:
                node.parent.expand()
            self.move_cursor(node)

    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        event.stop()
        channel_id = event.node.data
        store = self._store
        if channel_id is None or store is None:
            return
        channel = store.channels.get(channel_id)
        if channel is not None and channel.is_voice:
            self.notify(_("Voice channels aren't supported yet."))
        elif channel is not None and channel.is_readable:
            self.post_message(self.ChannelChosen(channel_id))
