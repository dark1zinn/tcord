from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import TextArea

from ..keymap import Keymap, event_tokens


class Composer(TextArea):
    """Message input: Enter sends, configurable keys insert newlines."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Cancelled(Message):
        pass

    class EditLast(Message):
        pass

    def __init__(self, keymap: Keymap, *, id: str | None = None) -> None:
        super().__init__(id=id, soft_wrap=True, tab_behavior="focus", show_line_numbers=False)
        self.keymap = keymap
        self.highlight_cursor_line = False

    async def _on_key(self, event: events.Key) -> None:
        tokens = event_tokens(event.key, event.character)
        if self.keymap.matches("composer.send", tokens):
            self._claim(event)
            self.post_message(self.Submitted(self.text))
        elif self.keymap.matches("composer.newline", tokens):
            self._claim(event)
            self.insert("\n")
        elif self.keymap.matches("composer.cancel", tokens):
            self._claim(event)
            self.post_message(self.Cancelled())
        elif not self.text and self.keymap.matches("composer.edit_last", tokens):
            self._claim(event)
            self.post_message(self.EditLast())
        else:
            await super()._on_key(event)

    @staticmethod
    def _claim(event: events.Key) -> None:
        event.stop()
        event.prevent_default()
