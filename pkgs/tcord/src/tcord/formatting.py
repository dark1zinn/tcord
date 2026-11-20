"""Presentation helpers: icons, names, times."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from client.models import Channel, ChannelKind, User

from .i18n import _


@dataclass(frozen=True, slots=True)
class Icons:
    unread: str
    expanded: str
    voice: str
    announcement: str
    attachment: str
    dm: str
    reply: str
    embed: str


UNICODE_ICONS = Icons("●", "▾", "🔊", "📢", "📎", "✉", "╭", "▏")
ASCII_ICONS = Icons("*", "v", "(v)", "(!)", "[file]", "DM", ",", "|")


def icons(ascii_only: bool) -> Icons:
    return ASCII_ICONS if ascii_only else UNICODE_ICONS


def format_time(moment: datetime, time_format: str) -> str:
    local = moment.astimezone()
    if time_format == "12h":
        return local.strftime("%I:%M %p").lstrip("0")
    return local.strftime("%H:%M")


def day_label(moment: datetime, today: date | None = None) -> str:
    day = moment.astimezone().date()
    current = today or datetime.now().astimezone().date()
    delta = (current - day).days
    if delta == 0:
        return _("Today")
    if delta == 1:
        return _("Yesterday")
    return day.isoformat()


def guild_abbreviation(name: str) -> str:
    words = [word for word in name.split() if word]
    if len(words) >= 2:
        return "".join(word[0] for word in words[:3]).upper()
    return name[:2] or "?"


def dm_name(channel: Channel, me: User | None = None) -> str:
    if channel.name:
        return channel.name
    names = [user.display_name for user in channel.recipients if me is None or user.id != me.id]
    return ", ".join(names) or _("Unnamed conversation")


def channel_title(channel: Channel, me: User | None = None) -> str:
    if channel.is_private:
        return "@" + dm_name(channel, me)
    return "#" + (channel.name or channel.id)


def channel_prefix(channel: Channel, ascii_only: bool) -> str:
    glyphs = icons(ascii_only)
    if channel.kind == ChannelKind.ANNOUNCEMENT:
        return glyphs.announcement + " "
    if channel.is_voice:
        return glyphs.voice + " "
    return "# "


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
