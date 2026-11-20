#!/usr/bin/env python3
"""Refresh the pinned Discord web-client identity used by tcord-engine.

Reads the current Discord client build number from the login page's JavaScript
assets and the current stable Chrome major version from Google's version
history API, then either reports the values (``--check``) or rewrites
``crates/tcord-engine/client-identity.json`` (``--write``).

Exit codes: 0 success (or nothing to change), 1 network/parse failure,
2 sanity check rejected the new values.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Final

LOGIN_URL: Final = "https://discord.com/login"
CHROME_VERSIONS_URL: Final = (
    "https://versionhistory.googleapis.com/v1/chrome/platforms/linux/channels/stable/versions"
)
REPO_ROOT: Final = Path(__file__).resolve().parents[1]
IDENTITY_PATH: Final = REPO_ROOT / "crates" / "tcord-engine" / "client-identity.json"
MAX_BUILD_INCREASE: Final = 200_000
TIMEOUT_SECONDS: Final = 30

PRIMARY_BUILD_RE: Final = re.compile(r"buildNumber\D{1,8}?(\d{5,7})")
FALLBACK_BUILD_RE: Final = re.compile(r"build_number[\"']?\s*[:=]\s*[\"']?(\d{5,7})")
SCRIPT_SRC_RE: Final = re.compile(r"<script[^>]*\bsrc=[\"'](/assets/[^\"']+\.js)[\"']")

EXIT_OK: Final = 0
EXIT_FETCH: Final = 1
EXIT_SANITY: Final = 2


class FetchError(Exception):
    """Network failure or unparseable upstream response."""


class SanityError(Exception):
    """Fetched values are implausible compared with the pinned ones."""


def extract_build_number(js: str) -> int | None:
    """Return the Discord client build number found in ``js``, if any."""
    for pattern in (PRIMARY_BUILD_RE, FALLBACK_BUILD_RE):
        match = pattern.search(js)
        if match is not None:
            return int(match.group(1))
    return None


def script_urls(html: str) -> list[str]:
    """Return the ``/assets/*.js`` script URLs of ``html`` in document order, deduplicated."""
    seen: dict[str, None] = {}
    for match in SCRIPT_SRC_RE.finditer(html):
        seen.setdefault(match.group(1), None)
    return list(seen)


def validate(current: int, new: int) -> None:
    """Reject build numbers that decrease or jump implausibly far from the pinned value."""
    if new < current:
        raise SanityError(
            f"build number {new} is lower than the pinned {current}; refusing to downgrade"
        )
    if new > current + MAX_BUILD_INCREASE:
        raise SanityError(
            f"build number {new} is more than {MAX_BUILD_INCREASE} above the pinned {current}; "
            "inspect the Discord assets manually before pinning"
        )


def render_identity(data: dict[str, Any]) -> str:
    """Serialize the identity file: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(data, sort_keys=True, indent=2) + "\n"


def chrome_version_for(major: int) -> str:
    """Return the ``<major>.0.0.0`` form stored in the identity file."""
    return f"{major}.0.0.0"


def user_agent_for(chrome_version: str) -> str:
    """Return the Linux Chrome UA the engine presents for ``chrome_version``."""
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{chrome_version} Safari/537.36"
    )


def major_of(version: str) -> int:
    """Return the major component of a dotted version string such as ``158.0.0.0``."""
    head, _, _ = version.partition(".")
    if not head.isdigit():
        raise FetchError(f"unexpected version string: {version!r}")
    return int(head)


def _get(url: str, *, accept: str, user_agent: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": accept})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body: bytes = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FetchError(f"GET {url} failed: {error}") from error
    return body


def load_identity(path: Path) -> dict[str, Any]:
    """Load the identity JSON object at ``path``."""
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FetchError(f"{path} does not contain a JSON object")
    return data


def fetch_build_number(user_agent: str) -> int:
    """Find the current Discord client build number in the login page's JavaScript."""
    html = _get(LOGIN_URL, accept="text/html", user_agent=user_agent).decode(
        "utf-8", errors="replace"
    )
    urls = script_urls(html)
    if not urls:
        raise FetchError("no /assets/*.js script tags found on the login page")
    # Later-listed bundles are the newest; try them first and stop at the first match.
    for path in reversed(urls):
        js = _get(f"https://discord.com{path}", accept="*/*", user_agent=user_agent).decode(
            "utf-8", errors="replace"
        )
        build = extract_build_number(js)
        if build is not None:
            return build
    raise FetchError("build number not found in any login page script")


def fetch_chrome_major(user_agent: str) -> int:
    """Return the major version of the newest stable Chrome on Linux."""
    body = _get(CHROME_VERSIONS_URL, accept="application/json", user_agent=user_agent)
    try:
        payload: Any = json.loads(body)
        versions: Any = payload["versions"]
        return major_of(str(versions[0]["version"]))
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise FetchError(f"unexpected Chrome version history response: {error}") from error


def _write_github_output(values: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with Path(target).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _pinned(path: Path) -> tuple[int, str, str]:
    """Return ``(build, chrome_version, user_agent)`` of the identity file at ``path``."""
    current = load_identity(path)
    chrome = str(current["chrome_version"])
    return int(current["client_build_number"]), chrome, user_agent_for(chrome)


def _check(path: Path) -> int:
    current_build, current_chrome, user_agent = _pinned(path)
    build = fetch_build_number(user_agent)
    chrome = chrome_version_for(fetch_chrome_major(user_agent))
    validate(current_build, build)
    changed = build != current_build or chrome != current_chrome
    values = {
        "changed": "true" if changed else "false",
        "client_build_number": str(build),
        "chrome_version": chrome,
        "previous_client_build_number": str(current_build),
        "previous_chrome_version": current_chrome,
    }
    _write_github_output(values)
    for key, value in values.items():
        print(f"{key}={value}")
    return EXIT_OK


def _write(path: Path) -> int:
    current_build, _, user_agent = _pinned(path)
    build = fetch_build_number(user_agent)
    chrome = chrome_version_for(fetch_chrome_major(user_agent))
    validate(current_build, build)
    data = {"client_build_number": build, "chrome_version": chrome}
    path.write_text(render_identity(data), encoding="utf-8")
    print(f"wrote {path}: client_build_number={build} chrome_version={chrome}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="report whether values changed")
    mode.add_argument("--write", action="store_true", help="rewrite the identity file")
    parser.add_argument("--file", type=Path, default=IDENTITY_PATH, help="identity JSON path")
    args = parser.parse_args(argv)
    try:
        if args.check:
            return _check(args.file)
        return _write(args.file)
    except FetchError as error:
        print(f"update-client-identity: {error}", file=sys.stderr)
        return EXIT_FETCH
    except SanityError as error:
        print(f"update-client-identity: {error}", file=sys.stderr)
        return EXIT_SANITY


if __name__ == "__main__":
    raise SystemExit(main())
