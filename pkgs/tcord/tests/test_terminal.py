"""tmux probing and pane passthrough, driven through a fake tmux command runner."""

from __future__ import annotations

import signal

import pytest
from fakes import FakeTmux
from tcord.terminal import Tmux, restore_on_signals, wrap_for_tmux


@pytest.mark.parametrize(
    ("client", "kitty"),
    [
        ("xterm-kitty kitty(0.49.2)", True),
        ("xterm-256color ghostty(1.2.0)", True),  # TERM overridden: the termtype still tells
        ("xterm-256color foot(1.20)", False),
        ("", False),
    ],
)
def test_outer_terminal_detection(client: str, kitty: bool) -> None:
    assert Tmux("%1", FakeTmux(client)).outer_supports_kitty() is kitty


def test_passthrough_is_enabled_for_the_pane_and_restored() -> None:
    fake = FakeTmux()
    tmux = Tmux("%1", fake)
    assert not tmux.passthrough()
    assert tmux.enable_passthrough()
    assert tmux.passthrough()
    assert fake.sets == [("set", "-p", "-t", "%1", "allow-passthrough", "on")]
    tmux.restore_passthrough()
    assert fake.sets[-1] == ("set", "-pu", "-t", "%1", "allow-passthrough")  # was inherited
    assert not tmux.passthrough()
    tmux.restore_passthrough()  # idempotent
    assert len(fake.sets) == 2


def test_explicit_pane_value_is_put_back() -> None:
    fake = FakeTmux(pane_value="off")
    tmux = Tmux("%1", fake)
    tmux.enable_passthrough()
    tmux.restore_passthrough()
    assert fake.sets[-1] == ("set", "-p", "-t", "%1", "allow-passthrough", "off")


def test_passthrough_already_on_is_left_alone() -> None:
    fake = FakeTmux()
    fake.global_value = "on"
    tmux = Tmux("%1", fake)
    assert tmux.enable_passthrough()
    tmux.restore_passthrough()
    assert fake.sets == []


def test_failed_set_reports_no_passthrough() -> None:
    fake = FakeTmux()
    fake.fail_sets = True
    tmux = Tmux("%1", fake)
    assert not tmux.enable_passthrough()
    tmux.restore_passthrough()
    assert fake.sets == []


def test_wrap_doubles_escapes_per_command() -> None:
    sequence = "\x1b_Ga=T,m=1;AAAA\x1b\\\x1b_Gm=0;BBBB\x1b\\"
    assert wrap_for_tmux(sequence) == (
        "\x1bPtmux;\x1b\x1b_Ga=T,m=1;AAAA\x1b\x1b\\\x1b\\"
        "\x1bPtmux;\x1b\x1b_Gm=0;BBBB\x1b\x1b\\\x1b\\"
    )


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="POSIX signals")
def test_signal_restores_passthrough_then_lets_the_signal_through() -> None:
    fake = FakeTmux()
    tmux = Tmux("%1", fake)
    tmux.enable_passthrough()
    forwarded: list[int] = []
    previous = signal.getsignal(signal.SIGUSR1)
    try:
        restore_on_signals(tmux, [signal.SIGUSR1], kill=lambda _pid, sig: forwarded.append(sig))
        signal.raise_signal(signal.SIGUSR1)
        assert fake.sets[-1] == ("set", "-pu", "-t", "%1", "allow-passthrough")
        assert forwarded == [signal.SIGUSR1]  # then the default action runs
        assert signal.getsignal(signal.SIGUSR1) == signal.SIG_DFL
    finally:
        signal.signal(signal.SIGUSR1, previous)
