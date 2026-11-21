"""Terminal image rendering.

Two renderers produce ordinary :class:`rich.text.Text`, so images can live inside any text
widget (option lists, statics) and scroll with it:

* **kitty**: the kitty graphics protocol with *unicode placeholders*. The image is uploaded
  once with an escape sequence and a virtual placement; it is then drawn by printing
  ``U+10EEEE`` cells whose combining diacritics encode the row and column and whose
  foreground colour encodes the image id. Supported by kitty and Ghostty.
* **halfblock**: each cell shows two vertical pixels with ``▀`` (foreground = upper pixel,
  background = lower pixel). Works in any truecolor terminal.

Spec: https://sw.kovidgoyal.net/kitty/graphics-protocol/#unicode-placeholders
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from PIL import Image, ImageSequence
from rich.color import Color
from rich.style import Style
from rich.text import Text

from ._diacritics import DIACRITICS

log = logging.getLogger(__name__)

PLACEHOLDER = "\U0010eeee"
MAX_CELLS = len(DIACRITICS)
MAX_FRAMES = 48
MIN_FRAME_SECONDS = 0.125  # cap animations at 8 fps
#: Approximate pixels per cell used when uploading to kitty (it rescales to the cell box).
KITTY_CELL_PX = (10, 20)
CACHE_SIZE = 400

type RGB = tuple[int, int, int]


class ImageProtocol(StrEnum):
    AUTO = "auto"
    KITTY = "kitty"
    HALFBLOCK = "halfblock"
    OFF = "off"


def detect_protocol(env: Mapping[str, str] | None = None) -> ImageProtocol:
    """Best renderer for the current terminal (never ``AUTO``)."""
    env = os.environ if env is None else env
    term = env.get("TERM", "")
    program = env.get("TERM_PROGRAM", "").lower()
    if "NO_COLOR" in env or term in ("", "dumb", "linux"):
        return ImageProtocol.OFF
    # Placeholders need the terminal itself; tmux/screen would have to forward the uploads.
    multiplexed = "TMUX" in env or term.startswith("screen")
    kitty = term == "xterm-kitty" or "KITTY_WINDOW_ID" in env
    ghostty = program == "ghostty" or term == "xterm-ghostty" or "GHOSTTY_RESOURCES_DIR" in env
    if (kitty or ghostty) and not multiplexed:
        return ImageProtocol.KITTY
    return ImageProtocol.HALFBLOCK


def resolve_protocol(setting: str, env: Mapping[str, str] | None = None) -> ImageProtocol:
    try:
        protocol = ImageProtocol(setting)
    except ValueError:
        protocol = ImageProtocol.AUTO
    return detect_protocol(env) if protocol is ImageProtocol.AUTO else protocol


def fit(width: int, height: int, max_cols: int, max_rows: int) -> tuple[int, int]:
    """Cell box for an image of ``width``x``height`` pixels (cells are twice as tall as wide)."""
    width, height = max(width, 1), max(height, 1)
    max_cols = max(1, min(max_cols, MAX_CELLS))
    max_rows = max(1, min(max_rows, MAX_CELLS))
    cols = max_cols
    rows = round(cols * height / width / 2)
    if rows > max_rows:
        rows = max_rows
        cols = round(rows * 2 * width / height)
    return max(1, min(cols, max_cols)), max(1, rows)


@dataclass(frozen=True, slots=True)
class Frame:
    text: Text
    seconds: float


@dataclass(frozen=True, slots=True)
class RenderedImage:
    frames: tuple[Frame, ...]
    cols: int
    rows: int
    image_ids: tuple[int, ...] = ()

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    def frame(self, index: int = 0) -> Text:
        return self.frames[index % len(self.frames)].text


@dataclass(frozen=True, slots=True)
class ImageKey:
    """What to draw: the source URL, the largest cell box, the backdrop for transparency."""

    url: str
    max_cols: int
    max_rows: int
    background: RGB = (0, 0, 0)
    animate: bool = False
    #: Stretch to exactly the box (icons, avatars) instead of preserving the aspect ratio.
    exact: bool = False


# ----- decoding (pure, runs in a worker thread) --------------------------------------------


def decode(data: bytes, *, animate: bool) -> list[tuple[Image.Image, float]]:
    """RGBA frames with display durations (seconds). Raises on undecodable data."""
    with Image.open(io.BytesIO(data)) as source:
        if not animate or not getattr(source, "is_animated", False):
            return [(source.convert("RGBA"), 0.0)]
        frames = []
        for index, frame in enumerate(ImageSequence.Iterator(source)):
            if index >= MAX_FRAMES:
                break
            seconds = max(frame.info.get("duration", 100) / 1000, MIN_FRAME_SECONDS)
            frames.append((frame.convert("RGBA"), seconds))
        return frames


def _flatten(image: Image.Image, background: RGB) -> Image.Image:
    base = Image.new("RGBA", image.size, (*background, 255))
    base.alpha_composite(image)
    return base.convert("RGB")


def halfblock(image: Image.Image, cols: int, rows: int, background: RGB) -> Text:
    scaled = image.resize((cols, rows * 2), Image.Resampling.LANCZOS)
    raw = _flatten(scaled, background).tobytes()

    def rgb(x: int, y: int) -> Color:
        offset = (y * cols + x) * 3
        return Color.from_rgb(raw[offset], raw[offset + 1], raw[offset + 2])

    text = Text(no_wrap=True, overflow="crop")
    for row in range(rows):
        for col in range(cols):
            text.append("▀", Style(color=rgb(col, row * 2), bgcolor=rgb(col, row * 2 + 1)))
        if row < rows - 1:
            text.append("\n")
    return text


def png(image: Image.Image, cols: int, rows: int) -> bytes:
    width, height = cols * KITTY_CELL_PX[0], rows * KITTY_CELL_PX[1]
    buffer = io.BytesIO()
    image.resize((width, height), Image.Resampling.LANCZOS).save(buffer, format="PNG")
    return buffer.getvalue()


def placeholder(image_id: int, cols: int, rows: int) -> Text:
    """Cells referencing a kitty virtual placement of ``image_id`` (24-bit id in the colour)."""
    style = Style(
        color=Color.from_rgb((image_id >> 16) & 0xFF, (image_id >> 8) & 0xFF, image_id & 0xFF)
    )
    lines = []
    for row in range(rows):
        lines.append(
            "".join(
                PLACEHOLDER + chr(DIACRITICS[row]) + chr(DIACRITICS[col]) for col in range(cols)
            )
        )
    return Text("\n".join(lines), style=style, no_wrap=True, overflow="crop")


def kitty_upload(image_id: int, data: bytes, cols: int, rows: int) -> str:
    """Escape sequences transmitting PNG ``data`` and creating a virtual placement."""
    encoded = base64.standard_b64encode(data).decode("ascii")
    chunks = [encoded[i : i + 4096] for i in range(0, len(encoded), 4096)] or [""]
    parts = []
    for index, chunk in enumerate(chunks):
        more = 1 if index < len(chunks) - 1 else 0
        if index == 0:
            control = f"a=T,U=1,f=100,t=d,i={image_id},c={cols},r={rows},q=2,m={more}"
        else:
            control = f"m={more}"
        parts.append(f"\x1b_G{control};{chunk}\x1b\\")
    return "".join(parts)


def kitty_delete(image_id: int) -> str:
    return f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\"


@dataclass(frozen=True, slots=True)
class _Prepared:
    cols: int
    rows: int
    seconds: tuple[float, ...]
    texts: tuple[Text, ...] = ()
    pngs: tuple[bytes, ...] = ()


# ----- store -------------------------------------------------------------------------------


class ImageStore:
    """Loads, renders and caches images; notifies ``on_ready`` with the URL when one is done.

    Images render lazily: :meth:`get` returns ``None`` until the bytes are fetched and decoded,
    so callers draw a placeholder and redraw on the notification.
    """

    def __init__(
        self,
        fetch: Callable[[str], Awaitable[bytes]],
        protocol: ImageProtocol,
        *,
        write: Callable[[str], None] | None = None,
        on_ready: Callable[[str], None] | None = None,
    ) -> None:
        self.fetch = fetch
        self.protocol = protocol
        self.write = write or (lambda _data: None)
        self.on_ready = on_ready or (lambda _url: None)
        self._cache: OrderedDict[ImageKey, RenderedImage] = OrderedDict()
        self._loading: dict[ImageKey, asyncio.Task[None]] = {}
        self._failed: set[ImageKey] = set()
        self._next_id = 1

    @property
    def enabled(self) -> bool:
        return self.protocol in (ImageProtocol.KITTY, ImageProtocol.HALFBLOCK)

    @property
    def supports_inline(self) -> bool:
        """Whether tiny inline images (emoji in text) are legible."""
        return self.protocol is ImageProtocol.KITTY

    def get(self, key: ImageKey) -> RenderedImage | None:
        if not self.enabled or key in self._failed:
            return None
        rendered = self._cache.get(key)
        if rendered is not None:
            self._cache.move_to_end(key)
            return rendered
        if key not in self._loading:
            self._loading[key] = asyncio.get_running_loop().create_task(self._load(key))
        return None

    def failed(self, key: ImageKey) -> bool:
        return key in self._failed

    async def _load(self, key: ImageKey) -> None:
        try:
            data = await self.fetch(key.url)
            prepared = await asyncio.to_thread(self._prepare, key, data)
            rendered = self._finish(prepared)
        except Exception as error:  # any fetch/decode failure just hides the image
            log.debug("image %s failed: %s", key.url, error)
            self._failed.add(key)
            return
        finally:
            self._loading.pop(key, None)
        self._cache[key] = rendered
        while len(self._cache) > CACHE_SIZE:
            _, evicted = self._cache.popitem(last=False)
            for image_id in evicted.image_ids:
                self.write(kitty_delete(image_id))
        self.on_ready(key.url)

    def _prepare(self, key: ImageKey, data: bytes) -> _Prepared:
        """Decode and scale (worker thread): half-block text or PNGs for kitty."""
        frames = decode(data, animate=key.animate)
        first = frames[0][0]
        if key.exact:
            cols, rows = max(1, key.max_cols), max(1, key.max_rows)
        else:
            cols, rows = fit(first.width, first.height, key.max_cols, key.max_rows)
        seconds = tuple(duration for _, duration in frames)
        if self.protocol is ImageProtocol.KITTY:
            pngs = tuple(png(image, cols, rows) for image, _ in frames)
            return _Prepared(cols, rows, seconds, pngs=pngs)
        texts = tuple(halfblock(image, cols, rows, key.background) for image, _ in frames)
        return _Prepared(cols, rows, seconds, texts=texts)

    def _finish(self, prepared: _Prepared) -> RenderedImage:
        """Upload kitty images (event loop, it writes to the terminal) and build frames."""
        if not prepared.pngs:
            frames = tuple(
                Frame(text, seconds)
                for text, seconds in zip(prepared.texts, prepared.seconds, strict=True)
            )
            return RenderedImage(frames, prepared.cols, prepared.rows)
        kitty_frames = []
        ids = []
        for data, seconds in zip(prepared.pngs, prepared.seconds, strict=True):
            image_id = self._allocate_id()
            self.write(kitty_upload(image_id, data, prepared.cols, prepared.rows))
            ids.append(image_id)
            kitty_frames.append(Frame(placeholder(image_id, prepared.cols, prepared.rows), seconds))
        return RenderedImage(tuple(kitty_frames), prepared.cols, prepared.rows, tuple(ids))

    def _allocate_id(self) -> int:
        image_id = self._next_id
        self._next_id = self._next_id % 0xFFFFFF + 1
        return image_id

    def clear(self) -> None:
        """Forget everything (logout, protocol change) and free terminal-side images."""
        for task in self._loading.values():
            task.cancel()
        self._loading.clear()
        for rendered in self._cache.values():
            for image_id in rendered.image_ids:
                self.write(kitty_delete(image_id))
        self._cache.clear()
        self._failed.clear()
