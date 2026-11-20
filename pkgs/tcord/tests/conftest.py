from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from textual.pilot import Pilot

sys.path.insert(0, str(Path(__file__).parent))


async def settle(pilot: Pilot, rounds: int = 6) -> None:
    """Let pending workers, timers and messages run (the auth/event workers never finish)."""
    for _ in range(rounds):
        await pilot.pause()


async def wait_until(pilot: Pilot, condition: Callable[[], bool], rounds: int = 60) -> None:
    for _ in range(rounds):
        if condition():
            return
        await pilot.pause(0.02)
    raise AssertionError("condition not reached")
