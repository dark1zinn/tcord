"""Key presets, per-action overrides and a chord (multi-key sequence) dispatcher.

Independent of Textual. A *token* is either a single printable character (``"G"``, ``"?"``),
matched against the typed character, or a key name with optional modifiers (``"ctrl+d"``,
``"alt+shift+down"``, ``"space"``), matched against the normalized key name. A *sequence* is
tokens separated by spaces (``"g g"``).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .i18n import N_, _

type KeySequence = tuple[str, ...]

CHORD_TIMEOUT = 1.0
_MODIFIER_ORDER = ("ctrl", "alt", "shift", "meta")


class Scope(StrEnum):
    """Where a binding is matched: by the screen dispatcher or by the composer itself."""

    SCREEN = "screen"
    COMPOSER = "composer"


class Group(StrEnum):
    GLOBAL = N_("Global")
    NAVIGATION = N_("Navigation")
    MESSAGES = N_("Messages")
    COMPOSER = N_("Composer")


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    group: Group
    description: str  # untranslated msgid; translate with ``_()`` when displayed
    scope: Scope = Scope.SCREEN


ACTIONS: dict[str, Action] = {
    action.id: action
    for action in (
        Action("app.quit", Group.GLOBAL, N_("Quit")),
        Action("app.switcher", Group.GLOBAL, N_("Quick switcher")),
        Action("app.palette", Group.GLOBAL, N_("Command palette")),
        Action("app.help", Group.GLOBAL, N_("Keyboard shortcuts")),
        Action("app.settings", Group.GLOBAL, N_("Settings")),
        Action("toggle.rail", Group.GLOBAL, N_("Toggle server rail")),
        Action("toggle.channels", Group.GLOBAL, N_("Toggle channel list")),
        Action("focus.rail", Group.NAVIGATION, N_("Focus server rail")),
        Action("focus.channels", Group.NAVIGATION, N_("Focus channel list")),
        Action("focus.messages", Group.NAVIGATION, N_("Focus messages")),
        Action("focus.composer", Group.NAVIGATION, N_("Focus composer")),
        Action("focus.left", Group.NAVIGATION, N_("Focus pane to the left")),
        Action("focus.right", Group.NAVIGATION, N_("Focus pane to the right")),
        Action("cursor.down", Group.NAVIGATION, N_("Move down")),
        Action("cursor.up", Group.NAVIGATION, N_("Move up")),
        Action("cursor.top", Group.NAVIGATION, N_("Go to top")),
        Action("cursor.bottom", Group.NAVIGATION, N_("Go to bottom")),
        Action("page.down", Group.NAVIGATION, N_("Page down")),
        Action("page.up", Group.NAVIGATION, N_("Page up")),
        Action("item.open", Group.NAVIGATION, N_("Open")),
        Action("item.back", Group.NAVIGATION, N_("Back")),
        Action("guild.next", Group.NAVIGATION, N_("Next server")),
        Action("guild.prev", Group.NAVIGATION, N_("Previous server")),
        Action("channel.next", Group.NAVIGATION, N_("Next channel")),
        Action("channel.prev", Group.NAVIGATION, N_("Previous channel")),
        Action("channel.next_unread", Group.NAVIGATION, N_("Next unread channel")),
        Action("channel.prev_unread", Group.NAVIGATION, N_("Previous unread channel")),
        Action("message.reply", Group.MESSAGES, N_("Reply")),
        Action("message.edit", Group.MESSAGES, N_("Edit message")),
        Action("message.delete", Group.MESSAGES, N_("Delete message")),
        Action("message.retry", Group.MESSAGES, N_("Retry failed message")),
        Action("composer.send", Group.COMPOSER, N_("Send"), Scope.COMPOSER),
        Action("composer.newline", Group.COMPOSER, N_("New line"), Scope.COMPOSER),
        Action("composer.cancel", Group.COMPOSER, N_("Cancel reply or edit"), Scope.COMPOSER),
        Action(
            "composer.edit_last",
            Group.COMPOSER,
            N_("Edit last message (empty composer)"),
            Scope.COMPOSER,
        ),
    )
}

# Actions bound as priority App bindings so they also work while typing in the composer.
GLOBAL_PRIORITY = ("app.quit", "app.switcher", "app.help")

_COMMON: dict[str, list[str]] = {
    "app.quit": ["ctrl+q"],
    "app.switcher": ["ctrl+k"],
    "app.palette": ["ctrl+p"],
    "app.help": ["f1"],
    "app.settings": ["f2"],
    "toggle.rail": ["alt+g"],
    "toggle.channels": ["alt+c"],
    "focus.rail": ["alt+1"],
    "focus.channels": ["alt+2"],
    "focus.messages": ["alt+3"],
    "focus.composer": ["alt+4"],
    "item.back": ["escape"],
    "message.retry": ["R"],
    "composer.send": ["enter"],
    "composer.newline": ["ctrl+j", "alt+enter", "shift+enter"],
    "composer.cancel": ["escape"],
    "composer.edit_last": ["up"],
}

_MODAL: dict[str, list[str]] = {
    "focus.composer": ["alt+4", "i", "a"],
    "focus.left": ["ctrl+w h"],
    "focus.right": ["ctrl+w l"],
    "cursor.down": ["j"],
    "cursor.up": ["k"],
    "cursor.top": ["g g"],
    "page.down": ["ctrl+d"],
    "page.up": ["ctrl+u"],
    "item.open": ["l"],
    "item.back": ["h", "escape"],
    "channel.next": ["] c"],
    "channel.prev": ["[ c"],
    "channel.next_unread": ["] u"],
    "channel.prev_unread": ["[ u"],
    "message.reply": ["r"],
}

PRESETS: dict[str, dict[str, list[str]]] = {
    "standard": {
        **_COMMON,
        "guild.next": ["ctrl+alt+down"],
        "guild.prev": ["ctrl+alt+up"],
        "channel.next": ["alt+down"],
        "channel.prev": ["alt+up"],
        "channel.next_unread": ["alt+shift+down"],
        "channel.prev_unread": ["alt+shift+up"],
        "message.reply": ["r"],
        "message.edit": ["e"],
        "message.delete": ["delete"],
    },
    "vim": {
        **_COMMON,
        **_MODAL,
        "app.switcher": ["ctrl+k", "/"],
        "app.palette": ["ctrl+p", ":"],
        "app.help": ["f1", "?"],
        "cursor.bottom": ["G"],
        "guild.next": ["g t"],
        "guild.prev": ["g T"],
        "message.edit": ["e"],
        "message.delete": ["d d"],
    },
    "helix": {
        **_COMMON,
        **_MODAL,
        "app.switcher": ["ctrl+k", "space f"],
        "app.palette": ["ctrl+p", "space ?"],
        "app.help": ["f1", "space h"],
        "app.settings": ["f2", "space s"],
        "cursor.bottom": ["g e"],
        "guild.next": ["g n"],
        "guild.prev": ["g p"],
        "message.edit": ["c"],
        "message.delete": ["d"],
    },
}

MODAL_PRESETS = frozenset({"vim", "helix"})

_KEY_ALIASES = {
    "esc": "escape",
    "return": "enter",
    "del": "delete",
    "pgdown": "pagedown",
    "pgup": "pageup",
    "spacebar": "space",
}


def normalize_token(token: str) -> str:
    """Canonical form: single characters verbatim, key names lowercased with sorted modifiers."""
    if len(token) == 1:
        return token
    parts = token.lower().split("+")
    if len(parts) > 1 and parts[-1] == "":
        parts = [*parts[:-2], "plus"]  # "ctrl++"
    *modifiers, key = parts
    if not key or any(part not in _MODIFIER_ORDER for part in modifiers):
        raise ValueError(token)
    ordered = sorted(set(modifiers), key=_MODIFIER_ORDER.index)
    return "+".join([*ordered, _KEY_ALIASES.get(key, key)])


def parse_sequence(text: str) -> KeySequence:
    tokens = text.split()
    if not tokens:
        raise ValueError(text)
    return tuple(normalize_token(token) for token in tokens)


def event_tokens(key: str, character: str | None) -> tuple[str, ...]:
    """Tokens a key event can match: the printable character first, then the key name."""
    tokens: list[str] = []
    if character is not None and len(character) == 1 and character.isprintable():
        tokens.append(character)
    try:
        normalized = normalize_token(key)
    except ValueError:
        normalized = key
    if normalized not in tokens:
        tokens.append(normalized)
    return tuple(tokens)


def display_sequence(sequence: KeySequence) -> str:
    return " ".join(sequence)


class Keymap:
    """Validated action -> key sequences mapping for one preset plus user overrides."""

    def __init__(self, preset: str, bindings: Mapping[str, tuple[KeySequence, ...]]) -> None:
        self.preset = preset
        self.bindings = dict(bindings)

    @property
    def modal(self) -> bool:
        return self.preset in MODAL_PRESETS

    @classmethod
    def from_settings(
        cls, preset: str, overrides: Mapping[str, Sequence[str]] | None = None
    ) -> tuple[Keymap, list[str]]:
        warnings: list[str] = []
        base = PRESETS.get(preset)
        if base is None:
            warnings.append(_("Unknown key preset {preset}; using standard.").format(preset=preset))
            preset, base = "standard", PRESETS["standard"]
        bindings = {action: tuple(map(parse_sequence, keys)) for action, keys in base.items()}
        for action, keys in (overrides or {}).items():
            if action not in ACTIONS:
                warnings.append(_("Unknown keymap action {action}").format(action=action))
                continue
            try:
                sequences = tuple(parse_sequence(key) for key in keys)
            except ValueError as error:
                warnings.append(
                    _("Invalid key {key} for {action}").format(key=error, action=action)
                )
                continue
            clash = _first_conflict(action, sequences, bindings)
            if clash is not None:
                warnings.append(
                    _("Key {key} for {action} conflicts with {other}; override ignored").format(
                        key=display_sequence(clash[0]), action=action, other=clash[1]
                    )
                )
                continue
            bindings[action] = sequences
        return cls(preset, bindings), warnings

    def sequences(self, action: str) -> tuple[KeySequence, ...]:
        return self.bindings.get(action, ())

    def display(self, action: str) -> str:
        return ", ".join(display_sequence(sequence) for sequence in self.sequences(action))

    def describe(self) -> list[str]:
        """One translated ``Description: keys`` line per bound action, grouped."""
        lines = []
        for group in Group:
            lines.append(_(group.value))
            lines.extend(
                f"  {_(action.description)}: {self.display(action.id)}"
                for action in ACTIONS.values()
                if action.group is group and self.sequences(action.id)
            )
        return lines

    def scoped(self, scope: Scope) -> dict[KeySequence, str]:
        return {
            sequence: action
            for action, sequences in self.bindings.items()
            if ACTIONS[action].scope is scope
            for sequence in sequences
        }

    def matches(self, action: str, tokens: Iterable[str]) -> bool:
        """Whether a single key event (its candidate tokens) triggers a one-key ``action``."""
        candidates = set(tokens)
        return any(len(seq) == 1 and seq[0] in candidates for seq in self.sequences(action))


def _first_conflict(
    action: str,
    sequences: tuple[KeySequence, ...],
    bindings: Mapping[str, tuple[KeySequence, ...]],
) -> tuple[KeySequence, str] | None:
    scope = ACTIONS[action].scope
    for other, others in bindings.items():
        if other == action or ACTIONS[other].scope is not scope:
            continue
        for sequence in sequences:
            for existing in others:
                shorter = min(len(sequence), len(existing))
                if sequence[:shorter] == existing[:shorter]:
                    return sequence, other
    return None


def preset_conflicts(preset: str) -> list[tuple[str, str]]:
    """Pairs of actions whose bindings collide in ``preset`` (used by tests)."""
    keymap, _warnings = Keymap.from_settings(preset)
    found = []
    for action, sequences in keymap.bindings.items():
        rest = {other: seqs for other, seqs in keymap.bindings.items() if other > action}
        clash = _first_conflict(action, sequences, rest)
        if clash is not None:
            found.append((action, clash[1]))
    return found


@dataclass(frozen=True, slots=True)
class Match:
    action: str


@dataclass(frozen=True, slots=True)
class Pending:
    prefix: KeySequence


@dataclass(frozen=True, slots=True)
class NoMatch:
    pass


type FeedResult = Match | Pending | NoMatch


class ChordDispatcher:
    """Resolves key events to actions, buffering multi-key prefixes for ``timeout`` seconds."""

    def __init__(
        self,
        bindings: Mapping[KeySequence, str],
        timeout: float = CHORD_TIMEOUT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout = timeout
        self._clock = clock
        self._pending: KeySequence = ()
        self._deadline = 0.0
        self.rebind(bindings)

    def rebind(self, bindings: Mapping[KeySequence, str]) -> None:
        self._exact = dict(bindings)
        self._prefixes = {
            sequence[:length] for sequence in bindings for length in range(1, len(sequence))
        }
        self.reset()

    @property
    def pending(self) -> KeySequence:
        if self._pending and self._clock() > self._deadline:
            self._pending = ()
        return self._pending

    def reset(self) -> None:
        self._pending = ()

    def feed(self, tokens: Sequence[str]) -> FeedResult:
        prefix = self.pending
        result = self._step(prefix, tokens)
        if isinstance(result, NoMatch) and prefix:
            # The chord broke: drop it and treat this key as the start of a new one.
            result = self._step((), tokens)
        if isinstance(result, Pending):
            self._pending = result.prefix
            self._deadline = self._clock() + self.timeout
        else:
            self._pending = ()
        return result

    def _step(self, prefix: KeySequence, tokens: Sequence[str]) -> FeedResult:
        for token in tokens:
            candidate = (*prefix, token)
            if candidate in self._exact:
                return Match(self._exact[candidate])
        for token in tokens:
            candidate = (*prefix, token)
            if candidate in self._prefixes:
                return Pending(candidate)
        return NoMatch()
