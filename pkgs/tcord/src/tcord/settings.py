"""User settings: typed defaults, TOML load with validation, atomic save."""

from __future__ import annotations

import os
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import platformdirs
import tomli_w

from .i18n import _

SCHEMA_VERSION = 1
LANGUAGES = ("auto", "en", "pt_BR", "es")
TIME_FORMATS = ("24h", "12h")
DENSITIES = ("cozy", "compact")
PRESETS = ("standard", "vim", "helix")
CHANNEL_LIST_WIDTH = (20, 48)
IMAGE_PROTOCOLS = ("auto", "kitty", "halfblock", "off")


@dataclass(frozen=True, slots=True)
class GeneralSettings:
    language: str = "auto"
    remember_session: bool = True


@dataclass(frozen=True, slots=True)
class AppearanceSettings:
    theme: str = "tcord-dark"
    time_format: str = "24h"
    show_timestamps: bool = True
    message_density: str = "cozy"
    ascii_only: bool = False
    reduced_motion: bool = False
    #: How images are drawn: auto-detect, kitty placeholders, half-blocks, or not at all.
    image_protocol: str = "auto"
    show_avatars: bool = True
    #: Attachments, embeds, stickers and custom emoji as images.
    show_images: bool = True
    #: Play GIFs and animated emoji/stickers on the highlighted message.
    animate_media: bool = True


@dataclass(frozen=True, slots=True)
class LayoutSettings:
    show_server_rail: bool = True
    show_channel_list: bool = True
    channel_list_width: int = 28
    #: List channels the account lacks permission to view (they are hidden by default).
    show_inaccessible_channels: bool = False


@dataclass(frozen=True, slots=True)
class NavigationSettings:
    preset: str = "standard"


def _empty_keymap() -> Mapping[str, tuple[str, ...]]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Settings:
    general: GeneralSettings = field(default_factory=GeneralSettings)
    appearance: AppearanceSettings = field(default_factory=AppearanceSettings)
    layout: LayoutSettings = field(default_factory=LayoutSettings)
    navigation: NavigationSettings = field(default_factory=NavigationSettings)
    # action id -> key sequences replacing the preset's bindings for that action
    keymap: Mapping[str, tuple[str, ...]] = field(default_factory=_empty_keymap)

    def to_toml(self) -> dict[str, Any]:
        document: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
        for name in ("general", "appearance", "layout", "navigation"):
            document[name] = asdict(getattr(self, name))
        document["keymap"] = {action: list(keys) for action, keys in self.keymap.items()}
        return document


type _Check = Callable[[Any], bool]


def _one_of(*choices: str) -> _Check:
    return lambda value: value in choices


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _width(value: Any) -> bool:
    low, high = CHANNEL_LIST_WIDTH
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


_SECTIONS: dict[str, tuple[type, dict[str, _Check]]] = {
    "general": (
        GeneralSettings,
        {"language": _one_of(*LANGUAGES), "remember_session": _is_bool},
    ),
    "appearance": (
        AppearanceSettings,
        {
            "theme": _non_empty_str,
            "time_format": _one_of(*TIME_FORMATS),
            "show_timestamps": _is_bool,
            "message_density": _one_of(*DENSITIES),
            "ascii_only": _is_bool,
            "reduced_motion": _is_bool,
            "image_protocol": _one_of(*IMAGE_PROTOCOLS),
            "show_avatars": _is_bool,
            "show_images": _is_bool,
            "animate_media": _is_bool,
        },
    ),
    "layout": (
        LayoutSettings,
        {
            "show_server_rail": _is_bool,
            "show_channel_list": _is_bool,
            "channel_list_width": _width,
            "show_inaccessible_channels": _is_bool,
        },
    ),
    "navigation": (NavigationSettings, {"preset": _one_of(*PRESETS)}),
}


def parse(document: Mapping[str, Any]) -> tuple[Settings, list[str]]:
    """Build settings from a TOML document; invalid or unknown entries become warnings."""
    invalid: list[str] = []
    sections: dict[str, Any] = {}
    unknown = [
        key for key in document if key not in ("schema_version", "keymap") and key not in _SECTIONS
    ]
    for name, (kind, checks) in _SECTIONS.items():
        raw = document.get(name, {})
        section = kind()
        if not isinstance(raw, Mapping):
            invalid.append(name)
            sections[name] = section
            continue
        values: dict[str, Any] = {}
        for key, value in raw.items():
            check = checks.get(key)
            if check is None:
                unknown.append(f"{name}.{key}")
            elif check(value):
                values[key] = value
            else:
                invalid.append(f"{name}.{key}")
        sections[name] = replace(section, **values)
    keymap: dict[str, tuple[str, ...]] = {}
    raw_keymap = document.get("keymap", {})
    if isinstance(raw_keymap, Mapping):
        for action, keys in raw_keymap.items():
            sequences = [keys] if isinstance(keys, str) else keys
            if isinstance(sequences, list) and all(_non_empty_str(item) for item in sequences):
                keymap[action] = tuple(item.strip() for item in sequences)
            else:
                invalid.append(f"keymap.{action}")
    else:
        invalid.append("keymap")
    warnings = []
    if invalid:
        warnings.append(
            _("Invalid settings reset to defaults: {keys}").format(keys=", ".join(invalid))
        )
    if unknown:
        warnings.append(_("Unknown settings ignored: {keys}").format(keys=", ".join(unknown)))
    return Settings(keymap=MappingProxyType(keymap), **sections), warnings


def default_path(config: Path | None = None) -> Path:
    if config is not None:
        return config
    if directory := os.environ.get("TCORD_CONFIG_DIR"):
        return Path(directory) / "settings.toml"
    return platformdirs.user_config_path("tcord") / "settings.toml"


class SettingsLockedError(Exception):
    """The settings file is malformed; overwriting it needs explicit confirmation."""


class SettingsFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.malformed = False

    def load(self) -> tuple[Settings, list[str]]:
        """Read the file; a missing file yields defaults and is not created."""
        try:
            document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Settings(), []
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
            self.malformed = True
            message = _("Couldn't read {path}: {error}. Using default settings.")
            return Settings(), [message.format(path=self.path, error=error)]
        self.malformed = False
        return parse(document)

    def save(self, settings: Settings, *, force: bool = False) -> None:
        """Write atomically (same-directory temp file + rename), owner-only permissions."""
        if self.malformed and not force:
            raise SettingsLockedError(str(self.path))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = tomli_w.dumps(settings.to_toml())
        handle, temporary = tempfile.mkstemp(
            dir=self.path.parent, prefix=".settings-", suffix=".toml"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
            if os.name == "posix":
                os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        self.malformed = False
