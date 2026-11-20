from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ..i18n import _


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no question; dismisses with ``True`` only on explicit confirmation."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", show=False),
        Binding("y", "confirm", show=False),
        Binding("n", "cancel", show=False),
    ]

    def __init__(self, question: str, confirm_label: str | None = None) -> None:
        super().__init__(classes="dialog")
        self.question = question
        self.confirm_label = confirm_label or _("Confirm")

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.question, id="question")
            with Horizontal(id="buttons"):
                yield Button(_("Cancel"), id="cancel")
                yield Button(self.confirm_label, id="confirm", variant="error")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
