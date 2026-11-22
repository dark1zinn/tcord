from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Switch, TabbedContent, TabPane

from ..i18n import _
from ..settings import CHANNEL_LIST_WIDTH, Settings


def _row(label: str, widget: Select | Switch | Input) -> Horizontal:
    return Horizontal(Label(label, classes="setting-label"), widget, classes="setting-row")


class SettingsScreen(ModalScreen[Settings | None]):
    """Edit settings; dismisses with the new settings on save, ``None`` on cancel."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", show=False),
        Binding("ctrl+s", "save", show=False, priority=True),
    ]

    def __init__(
        self, settings: Settings, path: Path, themes: list[str], keymap_lines: list[str]
    ) -> None:
        super().__init__(classes="dialog")
        self.settings = settings
        self.path = path
        self.themes = sorted({*themes, settings.appearance.theme})
        self.keymap_lines = keymap_lines

    def compose(self) -> ComposeResult:
        general, appearance = self.settings.general, self.settings.appearance
        layout, navigation = self.settings.layout, self.settings.navigation
        with Vertical(id="dialog"):
            yield Static(_("Settings"), id="title")
            with TabbedContent():
                with TabPane(_("General"), id="tab-general"):
                    yield _row(
                        _("Language"),
                        Select(
                            [
                                (_("Automatic"), "auto"),
                                ("English", "en"),
                                ("Português (Brasil)", "pt_BR"),
                                ("Español", "es"),
                            ],
                            value=general.language,
                            allow_blank=False,
                            id="language",
                        ),
                    )
                    yield _row(
                        _("Remember me on this device"),
                        Switch(general.remember_session, id="remember_session"),
                    )
                with TabPane(_("Appearance"), id="tab-appearance"):
                    yield _row(
                        _("Theme"),
                        Select(
                            [(name, name) for name in self.themes],
                            value=appearance.theme,
                            allow_blank=False,
                            id="theme",
                        ),
                    )
                    yield _row(
                        _("Time format"),
                        Select(
                            [(_("24-hour"), "24h"), (_("12-hour"), "12h")],
                            value=appearance.time_format,
                            allow_blank=False,
                            id="time_format",
                        ),
                    )
                    yield _row(
                        _("Message density"),
                        Select(
                            [(_("Cozy"), "cozy"), (_("Compact"), "compact")],
                            value=appearance.message_density,
                            allow_blank=False,
                            id="message_density",
                        ),
                    )
                    yield _row(
                        _("Show timestamps"),
                        Switch(appearance.show_timestamps, id="show_timestamps"),
                    )
                    yield _row(
                        _("ASCII-only symbols"), Switch(appearance.ascii_only, id="ascii_only")
                    )
                    yield _row(
                        _("Reduce motion"), Switch(appearance.reduced_motion, id="reduced_motion")
                    )
                with TabPane(_("Layout"), id="tab-layout"):
                    yield _row(
                        _("Show server rail"),
                        Switch(layout.show_server_rail, id="show_server_rail"),
                    )
                    yield _row(
                        _("Show channel list"),
                        Switch(layout.show_channel_list, id="show_channel_list"),
                    )
                    yield _row(
                        _("Channel list width ({low}-{high})").format(
                            low=CHANNEL_LIST_WIDTH[0], high=CHANNEL_LIST_WIDTH[1]
                        ),
                        Input(str(layout.channel_list_width), type="integer", id="channel_width"),
                    )
                    yield _row(
                        _("Show channels you can't access"),
                        Switch(layout.show_inaccessible_channels, id="show_inaccessible_channels"),
                    )
                with TabPane(_("Media"), id="tab-media"):
                    yield _row(
                        _("Image display"),
                        Select(
                            [
                                (_("Automatic"), "auto"),
                                (_("Kitty graphics"), "kitty"),
                                (_("Half blocks"), "halfblock"),
                                (_("Off"), "off"),
                            ],
                            value=appearance.image_protocol,
                            allow_blank=False,
                            id="image_protocol",
                        ),
                    )
                    yield _row(
                        _("Show avatars"), Switch(appearance.show_avatars, id="show_avatars")
                    )
                    yield _row(
                        _("Show images, stickers and emoji"),
                        Switch(appearance.show_images, id="show_images"),
                    )
                    yield _row(
                        _("Animate the highlighted message"),
                        Switch(appearance.animate_media, id="animate_media"),
                    )
                    yield _row(
                        _("Let kitty images through tmux"),
                        Switch(appearance.tmux_passthrough, id="tmux_passthrough"),
                    )
                with TabPane(_("Navigation"), id="tab-navigation"):
                    yield _row(
                        _("Key preset"),
                        Select(
                            [(_("Standard"), "standard"), ("Vim", "vim"), ("Helix", "helix")],
                            value=navigation.preset,
                            allow_blank=False,
                            id="preset",
                        ),
                    )
                    yield Static(
                        _("Override single actions in the [keymap] table of {path}").format(
                            path=self.path
                        ),
                        id="keymap-path",
                        markup=False,
                    )
                    with VerticalScroll(id="keymap-list"):
                        yield Static("\n".join(self.keymap_lines), markup=False)
            with Horizontal(id="buttons"):
                yield Button(_("Cancel"), id="cancel")
                yield Button(_("Save"), id="save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        else:
            self.action_cancel()

    def collect(self) -> Settings | None:
        """Settings from the form, or ``None`` (with a notification) when a value is invalid."""
        width_text = self.query_one("#channel_width", Input).value
        low, high = CHANNEL_LIST_WIDTH
        if not width_text.isdigit() or not low <= int(width_text) <= high:
            self.notify(
                _("Channel list width must be between {low} and {high}.").format(
                    low=low, high=high
                ),
                severity="error",
            )
            return None

        def select(widget_id: str) -> str:
            return str(self.query_one(f"#{widget_id}", Select).value)

        def switch(widget_id: str) -> bool:
            return self.query_one(f"#{widget_id}", Switch).value

        current = self.settings
        return replace(
            current,
            general=replace(
                current.general,
                language=select("language"),
                remember_session=switch("remember_session"),
            ),
            appearance=replace(
                current.appearance,
                theme=select("theme"),
                time_format=select("time_format"),
                message_density=select("message_density"),
                show_timestamps=switch("show_timestamps"),
                ascii_only=switch("ascii_only"),
                reduced_motion=switch("reduced_motion"),
                image_protocol=select("image_protocol"),
                show_avatars=switch("show_avatars"),
                show_images=switch("show_images"),
                animate_media=switch("animate_media"),
                tmux_passthrough=switch("tmux_passthrough"),
            ),
            layout=replace(
                current.layout,
                show_server_rail=switch("show_server_rail"),
                show_channel_list=switch("show_channel_list"),
                channel_list_width=int(width_text),
                show_inaccessible_channels=switch("show_inaccessible_channels"),
            ),
            navigation=replace(current.navigation, preset=select("preset")),
        )

    def action_save(self) -> None:
        settings = self.collect()
        if settings is not None:
            self.dismiss(settings)

    def action_cancel(self) -> None:
        self.dismiss(None)
