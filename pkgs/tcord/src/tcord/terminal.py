"""The terminal around Tcord when it runs inside tmux.

Inside tmux, ``TERM`` names tmux itself, so the real terminal comes from the attached client.
Kitty graphics only reach that terminal when the pane allows passthrough; Tcord can turn it on
for its own pane while it runs and restore the previous value on exit.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Callable, Mapping, Sequence
from types import FrameType

type Runner = Callable[[Sequence[str]], str | None]

#: ``client_termtype`` prefixes / ``client_termname`` values of terminals with kitty graphics.
KITTY_TERMTYPES = ("kitty", "ghostty")
KITTY_TERMNAMES = ("xterm-kitty", "xterm-ghostty")
_PASSTHROUGH = "allow-passthrough"


def _run(args: Sequence[str]) -> str | None:
    """Output of a tmux command, or ``None`` when it fails."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=2, check=False)
    except OSError, subprocess.SubprocessError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


class Tmux:
    """The tmux pane Tcord runs in."""

    def __init__(self, pane: str | None, run: Runner = _run) -> None:
        self.pane = pane
        self._run = run
        #: Pane-level value before :meth:`enable_passthrough` (``""`` = inherited).
        self._saved: str | None = None

    @classmethod
    def detect(cls, env: Mapping[str, str], run: Runner = _run) -> Tmux | None:
        if "TMUX" not in env:
            return None
        return cls(env.get("TMUX_PANE"), run)

    def _target(self) -> list[str]:
        return ["-t", self.pane] if self.pane else []

    def outer_supports_kitty(self) -> bool:
        """Whether the attached client is a terminal with kitty graphics."""
        query = "#{client_termname} #{client_termtype}"
        found = self._run(["tmux", "display-message", "-p", query])
        if not found:
            return False
        name, _, kind = found.partition(" ")
        return name in KITTY_TERMNAMES or kind.lower().startswith(KITTY_TERMTYPES)

    def passthrough(self) -> bool:
        value = self._run(["tmux", "show", "-Apv", *self._target(), _PASSTHROUGH])
        return value in ("on", "all")

    def enable_passthrough(self) -> bool:
        """Allow passthrough in this pane (remembering the old value); ``True`` on success."""
        if self.passthrough():
            return True
        saved = self._run(["tmux", "show", "-pv", *self._target(), _PASSTHROUGH])
        if saved is None:
            return False
        if self._run(["tmux", "set", "-p", *self._target(), _PASSTHROUGH, "on"]) is None:
            return False
        self._saved = saved
        return True

    def restore_passthrough(self) -> None:
        """Undo :meth:`enable_passthrough` (no-op when it changed nothing)."""
        saved, self._saved = self._saved, None
        if saved is None:
            return
        if saved:
            self._run(["tmux", "set", "-p", *self._target(), _PASSTHROUGH, saved])
        else:
            self._run(["tmux", "set", "-pu", *self._target(), _PASSTHROUGH])


def wrap_for_tmux(sequence: str) -> str:
    """Wrap each kitty graphics command so tmux forwards it to the outer terminal."""
    commands = [part for part in sequence.split("\x1b\\") if part]
    return "".join(
        "\x1bPtmux;" + (command + "\x1b\\").replace("\x1b", "\x1b\x1b") + "\x1b\\"
        for command in commands
    )


def restore_on_signals(
    tmux: Tmux,
    signals: Sequence[signal.Signals] | None = None,
    *,
    kill: Callable[[int, int], None] = os.kill,
) -> None:
    """Restore the pane option when Tcord is killed or its terminal goes away.

    Closing the pane sends SIGHUP and ``kill`` sends SIGTERM; neither runs ``finally``
    blocks, so the handler restores first and then lets the signal act as usual.
    """
    if signals is None:
        names = ("SIGHUP", "SIGTERM")
        signals = [getattr(signal, name) for name in names if hasattr(signal, name)]

    def handler(signum: int, _frame: FrameType | None) -> None:
        tmux.restore_passthrough()
        signal.signal(signum, signal.SIG_DFL)
        kill(os.getpid(), signum)

    for item in signals:
        signal.signal(item, handler)
