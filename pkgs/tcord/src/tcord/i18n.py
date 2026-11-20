"""gettext-based translations.

Call :func:`setup` once at startup, before any UI is built; afterwards ``_``, ``ngettext`` and
``pgettext`` translate through the selected catalog. Strings are never formatted inside ``_()``:
use ``_("Hello {name}").format(name=...)`` so extraction sees the template.
"""

import gettext as _gettext
import os
from pathlib import Path

SUPPORTED = ("en", "pt_BR", "es")
LOCALE_DIR = Path(__file__).parent / "locale"
_ENGINE_LOCALES = {"en": "en-US", "pt_BR": "pt-BR", "es": "es-ES"}

_translations: _gettext.NullTranslations = _gettext.NullTranslations()
_language = "en"


def resolve(language: str) -> str:
    """Map a setting value (``auto`` or a code) to one of :data:`SUPPORTED`."""
    if language == "auto":
        language = next(
            (
                value
                for value in (os.environ.get(key, "") for key in ("LC_ALL", "LC_MESSAGES", "LANG"))
                if value and value not in ("C", "POSIX")
            ),
            "en",
        )
    language = language.split(".")[0].split("@")[0]
    if language in SUPPORTED:
        return language
    if language.lower().startswith("pt"):
        return "pt_BR"
    if language.lower().startswith("es"):
        return "es"
    return "en"


def setup(language: str) -> str:
    """Install the catalog for ``language`` and return the resolved language code."""
    global _translations, _language
    _language = resolve(language)
    _translations = _gettext.translation(
        "tcord", localedir=LOCALE_DIR, languages=[_language], fallback=True
    )
    return _language


def current() -> str:
    return _language


def engine_locale() -> str:
    """The ``X-Discord-Locale``/``Accept-Language`` value for the active language."""
    return _ENGINE_LOCALES[_language]


def _(message: str) -> str:
    return _translations.gettext(message)


def ngettext(singular: str, plural: str, count: int) -> str:
    return _translations.ngettext(singular, plural, count)


def pgettext(context: str, message: str) -> str:
    return _translations.pgettext(context, message)
