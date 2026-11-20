import gettext
from collections.abc import Iterator

import pytest
from tcord import i18n


@pytest.fixture(autouse=True)
def _clean_locale(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(key, raising=False)
    yield
    i18n.setup("en")


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"LANG": "pt_BR.UTF-8"}, "pt_BR"),
        ({"LANG": "pt_PT.UTF-8"}, "pt_BR"),
        ({"LANG": "es_MX.UTF-8"}, "es"),
        ({"LANG": "de_DE.UTF-8"}, "en"),
        ({"LANG": "C"}, "en"),
        ({}, "en"),
        ({"LANG": "es_ES.UTF-8", "LC_ALL": "pt_BR.UTF-8"}, "pt_BR"),
    ],
)
def test_auto_resolution(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert i18n.setup("auto") == expected


def test_explicit_and_unknown_languages() -> None:
    assert i18n.setup("es") == "es"
    assert i18n.engine_locale() == "es-ES"
    assert i18n.setup("klingon") == "en"
    assert i18n._("Untranslated text") == "Untranslated text"


@pytest.mark.parametrize("language", ["pt_BR", "es"])
def test_compiled_catalog_loads(language: str) -> None:
    catalog = gettext.translation("tcord", localedir=i18n.LOCALE_DIR, languages=[language])
    assert catalog.info()["language"] == language
