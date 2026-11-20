"""Render QR codes as terminal text (always dark modules on a light background)."""

from __future__ import annotations

import segno
from rich.text import Text

QR_STYLE = "black on white"
_HALF_BLOCKS = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}


def qr_text(data: str, *, ascii_only: bool = False, border: int = 2) -> Text:
    code = segno.make(data, error="l")
    rows = [[bool(module) for module in row] for row in code.matrix_iter(scale=1, border=border)]
    lines: list[str] = []
    if ascii_only:
        lines = ["".join("##" if dark else "  " for dark in row) for row in rows]
    else:
        width = len(rows[0])
        for top_index in range(0, len(rows), 2):
            top = rows[top_index]
            bottom = rows[top_index + 1] if top_index + 1 < len(rows) else [False] * width
            lines.append("".join(_HALF_BLOCKS[pair] for pair in zip(top, bottom, strict=True)))
    return Text("\n".join(lines), style=QR_STYLE, no_wrap=True)
