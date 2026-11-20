from __future__ import annotations

from client.models import ConnectionStatus
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ..i18n import _


def connection_text(status: ConnectionStatus, retry_in: float | None) -> str:
    match status:
        case ConnectionStatus.CONNECTING:
            return _("Connecting…")
        case ConnectionStatus.CONNECTED:
            return _("Connected")
        case ConnectionStatus.RESUMING:
            return _("Resuming…")
        case ConnectionStatus.RECONNECTING if retry_in:
            return _("Reconnecting in {seconds}s…").format(seconds=round(retry_in))
        case ConnectionStatus.RECONNECTING:
            return _("Reconnecting…")
        case _:
            return _("Disconnected")


class StatusBar(Static):
    """Mode badge, pending chord, unseen-messages hint, connection status and help hint."""

    mode: reactive[str] = reactive("")
    chord: reactive[str] = reactive("")
    connection: reactive[str] = reactive("")
    new_messages: reactive[bool] = reactive(False)

    def render(self) -> Text:
        text = Text()
        if self.mode:
            text.append(f" {self.mode} ", style="bold reverse")
            text.append(" ")
        if self.chord:
            text.append(self.chord + " ", style="bold")
        if self.new_messages:
            text.append(_("New messages ↓") + "  ", style="bold")
        text.append(self.connection, style="dim")
        text.append("  " + _("F1 help · Ctrl+K switch"), style="dim")
        return text
