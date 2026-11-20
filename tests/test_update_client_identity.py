from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_client_identity.py"
_spec = importlib.util.spec_from_file_location("update_client_identity", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
updater = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(updater)

SENTRY_JS = 'var x=1;e.exports={buildNumber:"677695",releaseChannel:"stable"};'
FALLBACK_JS = 'config = { build_number: "512345" };'
LOGIN_HTML = """
<html><head>
<script src="/assets/vendor.aaaa.js" defer></script>
<script src='/assets/sentry.bbbb.js'></script>
<script src="https://cdn.example/other.js"></script>
<script src="/assets/vendor.aaaa.js"></script>
</head></html>
"""


def test_extracts_build_number_from_primary_pattern() -> None:
    assert updater.extract_build_number(SENTRY_JS) == 677695


def test_extracts_build_number_from_fallback_pattern() -> None:
    assert updater.extract_build_number(FALLBACK_JS) == 512345


def test_no_build_number_yields_none() -> None:
    assert updater.extract_build_number("var build = 12;") is None


def test_script_urls_are_assets_in_document_order_without_duplicates() -> None:
    assert updater.script_urls(LOGIN_HTML) == ["/assets/vendor.aaaa.js", "/assets/sentry.bbbb.js"]


def test_validate_accepts_same_and_bounded_increase() -> None:
    updater.validate(677695, 677695)
    updater.validate(677695, 677695 + updater.MAX_BUILD_INCREASE)


def test_validate_rejects_downgrade() -> None:
    with pytest.raises(updater.SanityError, match="downgrade"):
        updater.validate(677695, 677694)


def test_validate_rejects_wild_jump() -> None:
    with pytest.raises(updater.SanityError, match="above the pinned"):
        updater.validate(677695, 677695 + updater.MAX_BUILD_INCREASE + 1)


def test_render_identity_is_sorted_indented_with_trailing_newline() -> None:
    text = updater.render_identity({"lowercase_z": 1, "chrome_version": "158.0.0.0"})
    assert text == '{\n  "chrome_version": "158.0.0.0",\n  "lowercase_z": 1\n}\n'


def test_user_agent_mirrors_engine_format() -> None:
    assert updater.user_agent_for("158.0.0.0") == (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/158.0.0.0 Safari/537.36"
    )


def _identity(tmp_path: Path, build: int = 677695, chrome: str = "158.0.0.0") -> Path:
    path = tmp_path / "client-identity.json"
    path.write_text(
        json.dumps({"chrome_version": chrome, "client_build_number": build}), encoding="utf-8"
    )
    return path


def _stub_fetch(monkeypatch: pytest.MonkeyPatch, build: int | Exception, major: int = 158) -> None:
    def fake_build(_user_agent: str) -> int:
        if isinstance(build, Exception):
            raise build
        return build

    monkeypatch.setattr(updater, "fetch_build_number", fake_build)
    monkeypatch.setattr(updater, "fetch_chrome_major", lambda _user_agent: major)


def test_check_reports_change_and_writes_github_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    _stub_fetch(monkeypatch, build=677800)

    assert updater.main(["--check", "--file", str(_identity(tmp_path))]) == 0

    assert "changed=true" in output.read_text(encoding="utf-8")
    assert "client_build_number=677800" in capsys.readouterr().out


def test_check_unchanged_reports_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    _stub_fetch(monkeypatch, build=677695)

    assert updater.main(["--check", "--file", str(_identity(tmp_path))]) == 0
    assert "changed=false" in capsys.readouterr().out


def test_check_rejects_implausible_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    _stub_fetch(monkeypatch, build=1)

    assert updater.main(["--check", "--file", str(_identity(tmp_path))]) == 2
    assert "refusing to downgrade" in capsys.readouterr().err


def test_fetch_failure_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    _stub_fetch(monkeypatch, build=updater.FetchError("boom"))

    assert updater.main(["--check", "--file", str(_identity(tmp_path))]) == 1
    assert "boom" in capsys.readouterr().err


def test_write_rewrites_identity_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _identity(tmp_path)
    _stub_fetch(monkeypatch, build=677800, major=159)

    assert updater.main(["--write", "--file", str(path)]) == 0

    assert path.read_text(encoding="utf-8") == (
        '{\n  "chrome_version": "159.0.0.0",\n  "client_build_number": 677800\n}\n'
    )


def test_write_rejects_implausible_build_and_keeps_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _identity(tmp_path)
    before = path.read_text(encoding="utf-8")
    _stub_fetch(monkeypatch, build=updater.MAX_BUILD_INCREASE * 10)

    assert updater.main(["--write", "--file", str(path)]) == 2
    assert path.read_text(encoding="utf-8") == before
