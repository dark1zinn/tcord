"""Discord-inspired Textual themes."""

from textual.app import App
from textual.theme import Theme

THEMES = (
    Theme(
        name="tcord-dark",
        primary="#5865F2",
        accent="#5865F2",
        success="#23a55a",
        warning="#f0b232",
        error="#f23f43",
        foreground="#dbdee1",
        background="#313338",
        surface="#2b2d31",
        panel="#1e1f22",
        dark=True,
    ),
    Theme(
        name="tcord-light",
        primary="#5865F2",
        accent="#5865F2",
        success="#23a55a",
        warning="#c27c0e",
        error="#da373c",
        foreground="#313338",
        background="#ffffff",
        surface="#f2f3f5",
        panel="#e3e5e8",
        dark=False,
    ),
    Theme(
        name="tcord-high-contrast",
        primary="#ffff00",
        accent="#00ffff",
        success="#00ff00",
        warning="#ffaa00",
        error="#ff5555",
        foreground="#ffffff",
        background="#000000",
        surface="#000000",
        panel="#1a1a1a",
        dark=True,
    ),
)


def register_themes(app: App) -> None:
    for theme in THEMES:
        app.register_theme(theme)
