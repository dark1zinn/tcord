import pytest
from tcord.keymap import (
    ACTIONS,
    PRESETS,
    ChordDispatcher,
    Keymap,
    Match,
    NoMatch,
    Pending,
    Scope,
    event_tokens,
    normalize_token,
    preset_conflicts,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def dispatcher(preset: str = "vim", **overrides: list[str]) -> tuple[ChordDispatcher, Clock]:
    keymap, warnings = Keymap.from_settings(preset, overrides)
    assert warnings == []
    clock = Clock()
    return ChordDispatcher(keymap.scoped(Scope.SCREEN), clock=clock), clock


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_presets_are_conflict_free_and_known(preset: str) -> None:
    assert preset_conflicts(preset) == []
    assert set(PRESETS[preset]) <= set(ACTIONS)


def test_chord_match_and_pending() -> None:
    chords, _ = dispatcher()
    assert chords.feed(event_tokens("g", "g")) == Pending(("g",))
    assert chords.feed(event_tokens("g", "g")) == Match("cursor.top")
    assert chords.feed(event_tokens("j", "j")) == Match("cursor.down")


def test_expired_prefix_is_discarded() -> None:
    chords, clock = dispatcher()
    chords.feed(event_tokens("g", "g"))
    clock.now = 1.5
    assert chords.pending == ()
    assert chords.feed(event_tokens("t", "t")) == NoMatch()


def test_broken_chord_restarts_with_current_key() -> None:
    chords, _ = dispatcher()
    chords.feed(event_tokens("g", "g"))
    assert chords.feed(event_tokens("j", "j")) == Match("cursor.down")


def test_character_tokens_match_typed_character() -> None:
    chords, _ = dispatcher()
    # Shift+g arrives as key "G" (or "shift+g") with character "G".
    assert chords.feed(event_tokens("shift+g", "G")) == Match("cursor.bottom")
    assert chords.feed(event_tokens("question_mark", "?")) == Match("app.help")


def test_space_leader_in_helix() -> None:
    chords, _ = dispatcher("helix")
    assert chords.feed(event_tokens("space", " ")) == Pending(("space",))
    assert chords.feed(event_tokens("f", "f")) == Match("app.switcher")


def test_modifier_order_is_normalized() -> None:
    assert normalize_token("shift+alt+Down") == "alt+shift+down"
    chords, _ = dispatcher("standard")
    assert chords.feed(event_tokens("shift+alt+down", None)) == Match("channel.next_unread")


def test_override_replaces_preset_binding() -> None:
    chords, _ = dispatcher("vim", **{"cursor.down": ["n"]})
    assert chords.feed(event_tokens("n", "n")) == Match("cursor.down")
    assert chords.feed(event_tokens("j", "j")) == NoMatch()


def test_conflicting_or_invalid_overrides_lose_with_warning() -> None:
    keymap, warnings = Keymap.from_settings(
        "vim",
        {
            "message.reply": ["g"],  # prefix of "g g"
            "message.edit": ["j"],  # taken by cursor.down
            "nope.action": ["x"],
            "app.quit": ["hyper+q"],
        },
    )
    assert keymap.display("message.reply") == "r"
    assert keymap.display("message.edit") == "e"
    assert len(warnings) == 4


def test_composer_scope_does_not_conflict_with_screen_keys() -> None:
    keymap, warnings = Keymap.from_settings("standard", {"composer.send": ["ctrl+s"]})
    assert warnings == []
    assert keymap.matches("composer.send", event_tokens("ctrl+s", None))
    assert ("ctrl+s",) not in keymap.scoped(Scope.SCREEN)


def test_unknown_preset_falls_back() -> None:
    keymap, warnings = Keymap.from_settings("emacs")
    assert keymap.preset == "standard"
    assert len(warnings) == 1
