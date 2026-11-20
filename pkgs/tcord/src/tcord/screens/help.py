from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ..i18n import _
from ..keymap import ACTIONS, Group, Keymap, event_tokens


class HelpScreen(ModalScreen[None]):
    """Effective key bindings, grouped; closes on Escape or any help binding."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "dismiss", show=False)]

    def __init__(self, keymap: Keymap) -> None:
        super().__init__(classes="dialog")
        self.keymap = keymap

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(_("Keyboard shortcuts"), id="title")
            with VerticalScroll(id="help-body"):
                yield Static(self._content())
            yield Static(_("Press Escape to close"), id="help-hint")

    def _content(self) -> Text:
        text = Text()
        for group in Group:
            actions = [
                action
                for action in ACTIONS.values()
                if action.group is group and self.keymap.sequences(action.id)
            ]
            if not actions:
                continue
            if text:
                text.append("\n")
            text.append(_(group.value) + "\n", style="bold underline")
            for action in actions:
                text.append(f"  {self.keymap.display(action.id):<28}", style="bold")
                text.append(_(action.description) + "\n")
        return text

    def on_key(self, event: events.Key) -> None:
        tokens = event_tokens(event.key, event.character)
        if self.keymap.matches("app.help", tokens) or event.character == "?":
            event.stop()
            event.prevent_default()
            self.dismiss(None)
