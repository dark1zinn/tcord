import os
import tomllib
from pathlib import Path

import pytest
from tcord.settings import (
    Settings,
    SettingsFile,
    SettingsLockedError,
    default_path,
    parse,
)


def test_defaults_for_missing_file(tmp_path: Path) -> None:
    file = SettingsFile(tmp_path / "settings.toml")
    settings, warnings = file.load()
    assert settings == Settings()
    assert warnings == []
    assert not file.path.exists()


@pytest.mark.parametrize(
    ("document", "attribute", "expected", "warned"),
    [
        ({"general": {"language": "pt_BR"}}, ("general", "language"), "pt_BR", None),
        ({"general": {"language": "fr"}}, ("general", "language"), "auto", "general.language"),
        ({"layout": {"channel_list_width": 48}}, ("layout", "channel_list_width"), 48, None),
        ({"layout": {"channel_list_width": 49}}, ("layout", "channel_list_width"), 28, "layout"),
        ({"layout": {"show_server_rail": "no"}}, ("layout", "show_server_rail"), True, "layout"),
        ({"navigation": {"preset": "helix"}}, ("navigation", "preset"), "helix", None),
        ({"navigation": {"preset": "emacs"}}, ("navigation", "preset"), "standard", "preset"),
        ({"appearance": "dark"}, ("appearance", "theme"), "tcord-dark", "appearance"),
    ],
)
def test_validation_matrix(
    document: dict,
    attribute: tuple[str, str],
    expected: object,
    warned: str | None,
) -> None:
    settings, warnings = parse(document)
    section, key = attribute
    assert getattr(getattr(settings, section), key) == expected
    if warned is None:
        assert warnings == []
    else:
        assert len(warnings) == 1
        assert warned in warnings[0]


def test_unknown_keys_and_keymap_types() -> None:
    settings, warnings = parse(
        {
            "colour": 1,
            "general": {"nickname": "x"},
            "keymap": {"app.quit": "ctrl+x", "app.help": ["f1", "?"], "app.palette": 3},
        }
    )
    assert settings.keymap == {"app.quit": ("ctrl+x",), "app.help": ("f1", "?")}
    assert any("keymap.app.palette" in warning for warning in warnings)
    assert any("colour" in warning and "general.nickname" in warning for warning in warnings)


def test_save_round_trip_is_atomic_and_private(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "settings.toml"
    file = SettingsFile(path)
    settings, _ = parse({"appearance": {"theme": "tcord-light"}, "keymap": {"app.quit": ["q"]}})

    file.save(settings)

    assert tomllib.loads(path.read_text())["schema_version"] == 1
    assert SettingsFile(path).load() == (settings, [])
    assert [item.name for item in path.parent.iterdir()] == ["settings.toml"]
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_malformed_file_is_not_overwritten_without_force(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("[general\n")
    file = SettingsFile(path)

    settings, warnings = file.load()
    assert settings == Settings()
    assert str(path) in warnings[0]

    with pytest.raises(SettingsLockedError):
        file.save(settings)
    assert path.read_text() == "[general\n"

    file.save(settings, force=True)
    assert SettingsFile(path).load() == (settings, [])


def test_path_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TCORD_CONFIG_DIR", str(tmp_path))
    assert default_path() == tmp_path / "settings.toml"
    explicit = tmp_path / "other.toml"
    assert default_path(explicit) == explicit
    monkeypatch.delenv("TCORD_CONFIG_DIR")
    assert default_path().name == "settings.toml"
