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

from client.errors import InvalidInputError, NotFoundError, PermissionDeniedError, TcordError
from PIL import Image, ImageSequence
from rich.color import Color
from rich.style import Style
from rich.text import Text

from ._diacritics import DIACRITICS
from .terminal import wrap_for_tmux

log = logging.getLogger(__name__)

PLACEHOLDER = "\U0010eeee"
MAX_CELLS = len(DIACRITICS)
MAX_FRAMES = 48
MIN_FRAME_SECONDS = 0.125  # cap animations at 8 fps
#: Approximate pixels per cell used when uploading to kitty (it rescales to the cell box).
KITTY_CELL_PX = (10, 20)
CACHE_SIZE = 400
#: Decoded bytes of kitty images Tcord keeps uploaded (kitty's own quota is 320 MB).
TERMINAL_BUDGET = 192 * 1024 * 1024
#: Largest source canvas decoded (width x height); bigger images are refused.
MAX_SOURCE_PIXELS = 4096 * 4096
#: Images decoded concurrently (each decode runs in a worker thread).
DECODERS = 2
#: Transient failures (network, rate limits) retry with exponential backoff.
RETRY_SECONDS = 5.0
MAX_RETRY_SECONDS = 300.0
MAX_ATTEMPTS = 5

type RGB = tuple[int, int, int]


class ImageProtocol(StrEnum):
    AUTO = "auto"
    KITTY = "kitty"
    HALFBLOCK = "halfblock"
    OFF = "off"


def detect_protocol(
    env: Mapping[str, str] | None = None, *, tmux_kitty: bool = False
) -> ImageProtocol:
    """Best renderer for the current terminal (never ``AUTO``).

    ``tmux_kitty`` says that Tcord runs in tmux, the attached terminal has kitty graphics
    and the pane passes them through (see :mod:`tcord.terminal`).
    """
    env = os.environ if env is None else env
    term = env.get("TERM", "")
    program = env.get("TERM_PROGRAM", "").lower()
    if "NO_COLOR" in env or term in ("", "dumb", "linux"):
        return ImageProtocol.OFF
    if "TMUX" in env:
        # TERM and KITTY_WINDOW_ID describe tmux (or the shell that started it), not the
        # terminal attached now.
        return ImageProtocol.KITTY if tmux_kitty else ImageProtocol.HALFBLOCK
    if term.startswith("screen"):
        return ImageProtocol.HALFBLOCK  # GNU screen cannot forward the uploads
    kitty = term == "xterm-kitty" or "KITTY_WINDOW_ID" in env
    ghostty = program == "ghostty" or term == "xterm-ghostty" or "GHOSTTY_RESOURCES_DIR" in env
    return ImageProtocol.KITTY if kitty or ghostty else ImageProtocol.HALFBLOCK


def resolve_protocol(
    setting: str, env: Mapping[str, str] | None = None, *, tmux_kitty: bool = False
) -> ImageProtocol:
    try:
        protocol = ImageProtocol(setting)
    except ValueError:
        protocol = ImageProtocol.AUTO
    if protocol is ImageProtocol.AUTO:
        return detect_protocol(env, tmux_kitty=tmux_kitty)
    return protocol


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
    #: Kitty image ids (one per frame) and the decoded bytes they keep in the terminal.
    image_ids: tuple[int, ...] = ()
    terminal_bytes: int = 0

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


def decode(
    data: bytes, *, animate: bool, limit: tuple[int, int]
) -> tuple[tuple[int, int], list[tuple[Image.Image, float]]]:
    """Source size plus RGBA frames with display durations (seconds).

    Each frame is shrunk to fit ``limit`` (pixels) as soon as it is decoded, so a large
    animation never holds more than one full-size frame. Raises on undecodable data.
    """

    def shrink(frame: Image.Image) -> Image.Image:
        rgba = frame.convert("RGBA")
        if limit is not None:
            rgba.thumbnail(limit, Image.Resampling.LANCZOS)
        return rgba

    with Image.open(io.BytesIO(data)) as source:
        size = source.size
        if size[0] * size[1] > MAX_SOURCE_PIXELS:
            # Checked before any pixel is decoded: a tiny file can claim a huge canvas.
            raise ValueError(f"image too large: {size[0]}x{size[1]}")
        if not animate or not getattr(source, "is_animated", False):
            return size, [(shrink(source), 0.0)]
        frames = []
        for index, frame in enumerate(ImageSequence.Iterator(source)):
            if index >= MAX_FRAMES:
                break
            seconds = max(frame.info.get("duration", 100) / 1000, MIN_FRAME_SECONDS)
            frames.append((shrink(frame), seconds))
        return size, frames


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


class _Permanent(Exception):
    """A fetch failure that retrying cannot fix."""


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
        on_evicted: Callable[[set[str]], None] | None = None,
        tmux: bool = False,
        terminal_budget: int = TERMINAL_BUDGET,
    ) -> None:
        self.fetch = fetch
        self.protocol = protocol
        self.write = write or (lambda _data: None)
        self.on_ready = on_ready or (lambda _url: None)
        self.on_evicted = on_evicted or (lambda _urls: None)
        #: Wrap kitty commands so tmux passes them through to the outer terminal.
        self.tmux = tmux
        #: Decoded bytes of kitty images kept in the terminal; the oldest are deleted beyond
        #: this (kitty itself would silently drop images past its own quota).
        self.terminal_budget = terminal_budget
        self._resident = 0
        self._cache: OrderedDict[ImageKey, RenderedImage] = OrderedDict()
        self._loading: dict[ImageKey, asyncio.Task[None]] = {}
        self._failed: set[ImageKey] = set()
        #: Transient failures: attempts so far and when the next one may start.
        self._attempts: dict[ImageKey, int] = {}
        self._retry_at: dict[ImageKey, float] = {}
        #: Decoding runs in threads; bound how many images are decoded at once.
        self._decoders = asyncio.Semaphore(DECODERS)
        self._uploaded: set[int] = set()
        self._next_id = 1

    @property
    def enabled(self) -> bool:
        return self.protocol in (ImageProtocol.KITTY, ImageProtocol.HALFBLOCK)

    @property
    def supports_inline(self) -> bool:
        """Whether tiny inline images (emoji in text) are legible."""
        return self.protocol is ImageProtocol.KITTY

    def get(self, key: ImageKey, *, load: bool = True) -> RenderedImage | None:
        """The rendered image, else ``None``; starts loading it unless ``load`` is false."""
        if not self.enabled or key in self._failed:
            return None
        rendered = self._cache.get(key)
        if rendered is not None:
            self._cache.move_to_end(key)
            return rendered
        loop = asyncio.get_running_loop()
        blocked = key in self._loading or self._retry_at.get(key, 0.0) > loop.time()
        if load and not blocked:
            self._loading[key] = loop.create_task(self._load(key))
        return None

    def failed(self, key: ImageKey) -> bool:
        return key in self._failed

    async def settled(self) -> None:
        """Wait until every in-flight load has finished (successfully or not)."""
        while self._loading:
            await asyncio.gather(*self._loading.values(), return_exceptions=True)

    def _retry_later(self, key: ImageKey) -> None:
        """Back off, then ask the owners to redraw so the next :meth:`get` loads again."""
        attempts = self._attempts.get(key, 0) + 1
        self._attempts[key] = attempts
        if attempts > MAX_ATTEMPTS:
            self._failed.add(key)
            return
        delay = min(RETRY_SECONDS * 2 ** (attempts - 1), MAX_RETRY_SECONDS)
        loop = asyncio.get_running_loop()
        self._retry_at[key] = loop.time() + delay
        loop.call_later(delay, self.on_ready, key.url)

    async def _load(self, key: ImageKey) -> None:
        try:
            try:
                data = await self.fetch(key.url)
            except (NotFoundError, PermissionDeniedError, InvalidInputError) as error:
                raise _Permanent from error  # gone, forbidden, or refused: retrying won't help
            async with self._decoders:
                prepared = await asyncio.to_thread(self._prepare, key, data)
            rendered = self._finish(prepared)
        except Exception as error:
            log.debug("image %s failed: %s", key.url, error)
            if isinstance(error, TcordError):
                self._retry_later(key)  # network trouble, rate limits: try again later
            else:
                self._failed.add(key)  # undecodable, too large, or permanently unavailable
            return
        finally:
            # A cancelled load (clear()) must not drop the bookkeeping of its replacement.
            if self._loading.get(key) is asyncio.current_task():
                del self._loading[key]
        self._cache[key] = rendered
        self._resident += rendered.terminal_bytes
        self._evict()
        self._attempts.pop(key, None)
        self._retry_at.pop(key, None)
        self.on_ready(key.url)

    def _evict(self) -> None:
        """Keep the cache within :data:`CACHE_SIZE` and kitty images within the budget.

        Evicted kitty images are deleted from the terminal, so prompts that still draw them
        must be redrawn: ``on_evicted`` names their URLs.
        """
        evicted: set[str] = set()
        while len(self._cache) > CACHE_SIZE or (
            self._resident > self.terminal_budget and len(self._cache) > 1
        ):
            key, rendered = self._cache.popitem(last=False)
            if rendered.image_ids:
                for image_id in rendered.image_ids:
                    self._write(kitty_delete(image_id))
                    self._uploaded.discard(image_id)
                self._resident -= rendered.terminal_bytes
                evicted.add(key.url)
        if evicted:
            self.on_evicted(evicted)

    def _prepare(self, key: ImageKey, data: bytes) -> _Prepared:
        """Decode and scale (worker thread): half-block text or PNGs for kitty."""
        if self.protocol is ImageProtocol.KITTY:
            limit = (key.max_cols * KITTY_CELL_PX[0], key.max_rows * KITTY_CELL_PX[1])
        else:
            limit = (key.max_cols, key.max_rows * 2)
        size, frames = decode(data, animate=key.animate, limit=limit)
        if key.exact:
            cols, rows = max(1, key.max_cols), max(1, key.max_rows)
        else:
            cols, rows = fit(*size, key.max_cols, key.max_rows)
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
            self._write(kitty_upload(image_id, data, prepared.cols, prepared.rows))
            self._uploaded.add(image_id)
            ids.append(image_id)
            kitty_frames.append(Frame(placeholder(image_id, prepared.cols, prepared.rows), seconds))
        # Kitty keeps the decoded RGBA pixels of every frame.
        pixels = prepared.cols * KITTY_CELL_PX[0] * prepared.rows * KITTY_CELL_PX[1]
        return RenderedImage(
            tuple(kitty_frames), prepared.cols, prepared.rows, tuple(ids), pixels * 4 * len(ids)
        )

    def _allocate_id(self) -> int:
        image_id = self._next_id
        self._next_id = self._next_id % 0xFFFFFF + 1
        return image_id

    def _write(self, sequence: str) -> None:
        self.write(wrap_for_tmux(sequence) if self.tmux else sequence)

    def clear(self) -> None:
        """Forget everything (logout, protocol change) and free terminal-side images."""
        for task in self._loading.values():
            task.cancel()
        self._loading.clear()
        for image_id in self._uploaded:
            self._write(kitty_delete(image_id))
        self._uploaded.clear()
        self._resident = 0
        self._cache.clear()
        self._failed.clear()
        self._attempts.clear()
        self._retry_at.clear()
