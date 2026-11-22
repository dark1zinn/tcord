import asyncio
import io
import time
import types

import pytest
from client.errors import InvalidInputError, NetworkError, NotFoundError
from PIL import Image
from rich.cells import cell_len
from rich.style import Style
from tcord._diacritics import DIACRITICS
from tcord.images import (
    DECODERS,
    KITTY_CELL_PX,
    PLACEHOLDER,
    ImageKey,
    ImageProtocol,
    ImageStore,
    decode,
    detect_protocol,
    fit,
    halfblock,
    kitty_upload,
    placeholder,
    resolve_protocol,
)


def png_bytes(width: int, height: int, color: tuple[int, int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def gif_bytes(frames: int, *, side: int = 4) -> bytes:
    images = [Image.new("RGB", (side, side), (i * 40, 0, 0)) for i in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="GIF", save_all=True, append_images=images[1:], duration=50)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"TERM": "xterm-kitty"}, ImageProtocol.KITTY),
        ({"TERM": "xterm-256color", "TERM_PROGRAM": "ghostty"}, ImageProtocol.KITTY),
        ({"TERM": "xterm-kitty", "TMUX": "/tmp/t"}, ImageProtocol.HALFBLOCK),
        ({"TERM": "xterm-256color", "COLORTERM": "truecolor"}, ImageProtocol.HALFBLOCK),
        ({"TERM": "dumb"}, ImageProtocol.OFF),
        ({"TERM": "xterm-256color", "NO_COLOR": "1"}, ImageProtocol.OFF),
    ],
)
def test_detect_protocol(env: dict[str, str], expected: ImageProtocol) -> None:
    assert detect_protocol(env) is expected


def test_tmux_uses_the_attached_terminal() -> None:
    inside = {"TERM": "tmux-256color", "TMUX": "/tmp/t", "KITTY_WINDOW_ID": "1"}
    assert detect_protocol(inside) is ImageProtocol.HALFBLOCK
    assert detect_protocol(inside, tmux_kitty=True) is ImageProtocol.KITTY
    assert resolve_protocol("auto", inside, tmux_kitty=True) is ImageProtocol.KITTY


def test_explicit_setting_overrides_detection() -> None:
    assert resolve_protocol("halfblock", {"TERM": "xterm-kitty"}) is ImageProtocol.HALFBLOCK
    assert resolve_protocol("bogus", {"TERM": "xterm-kitty"}) is ImageProtocol.KITTY


async def test_kitty_uploads_are_wrapped_inside_tmux() -> None:
    written: list[str] = []

    async def fetch(url: str) -> bytes:
        return png_bytes(4, 4, (1, 2, 3, 255))

    store = ImageStore(fetch, ImageProtocol.KITTY, write=written.append, tmux=True)
    store.get(ImageKey("https://cdn.discordapp.com/a.png", 2, 1, exact=True))
    await store.settled()
    assert written
    assert all(chunk.startswith("\x1bPtmux;\x1b\x1b_G") for chunk in written)


@pytest.mark.parametrize(
    ("size", "box", "cells"),
    [
        ((100, 100), (8, 8), (8, 4)),  # square: half as many rows (cells are 1:2)
        ((400, 100), (40, 15), (40, 5)),
        ((100, 400), (40, 10), (5, 10)),  # tall: limited by rows
        ((1, 1), (1, 1), (1, 1)),
    ],
)
def test_fit_keeps_aspect_within_box(
    size: tuple[int, int], box: tuple[int, int], cells: tuple[int, int]
) -> None:
    assert fit(*size, *box) == cells


def triplet(style: str | Style, attribute: str) -> tuple[int, int, int]:
    assert isinstance(style, Style)
    color = getattr(style, attribute)
    assert color is not None
    assert color.triplet is not None
    return tuple(color.triplet)


def test_halfblock_uses_two_pixels_per_cell_and_flattens_alpha() -> None:
    image = Image.new("RGBA", (1, 2))
    image.putpixel((0, 0), (255, 0, 0, 255))
    image.putpixel((0, 1), (0, 0, 0, 0))  # transparent -> background
    text = halfblock(image, 1, 1, (0, 0, 255))
    assert text.plain == "▀"
    style = text.spans[0].style
    assert triplet(style, "color") == (255, 0, 0)
    assert triplet(style, "bgcolor") == (0, 0, 255)


def test_placeholder_encodes_rows_columns_and_id() -> None:
    text = placeholder(0x010203, cols=2, rows=2)
    lines = text.plain.split("\n")
    assert lines[1] == (
        PLACEHOLDER
        + chr(DIACRITICS[1])
        + chr(DIACRITICS[0])
        + PLACEHOLDER
        + chr(DIACRITICS[1])
        + chr(DIACRITICS[1])
    )
    assert triplet(text.style, "color") == (1, 2, 3)


@pytest.mark.parametrize(("cols", "rows"), [(1, 1), (4, 2), (6, 3)])
def test_placeholder_cells_are_one_column_wide(cols: int, rows: int) -> None:
    lines = placeholder(5, cols, rows).plain.split("\n")
    assert [cell_len(line) for line in lines] == [cols] * rows


def test_kitty_upload_is_chunked_with_virtual_placement() -> None:
    sequence = kitty_upload(7, b"x" * 5000, cols=4, rows=2)
    chunks = sequence.split("\x1b\\")[:-1]
    assert len(chunks) == 2
    assert chunks[0].startswith("\x1b_Ga=T,U=1,f=100,t=d,i=7,c=4,r=2,q=2,m=1;")
    assert chunks[1].startswith("\x1b_Gm=0;")


async def test_store_loads_once_notifies_and_caches() -> None:
    calls: list[str] = []
    ready = asyncio.Event()

    async def fetch(url: str) -> bytes:
        calls.append(url)
        return png_bytes(8, 8, (10, 20, 30, 255))

    store = ImageStore(fetch, ImageProtocol.HALFBLOCK, on_ready=lambda _url: ready.set())
    key = ImageKey("https://cdn.discordapp.com/a.png", 4, 4)
    assert store.get(key) is None
    assert store.get(key) is None  # in flight: no second fetch
    await asyncio.wait_for(ready.wait(), 5)
    rendered = store.get(key)
    assert rendered is not None
    assert (rendered.cols, rendered.rows) == (4, 2)
    assert calls == [key.url]


async def test_store_remembers_failures() -> None:
    attempts = 0

    async def fetch(url: str) -> bytes:
        nonlocal attempts
        attempts += 1
        return b"not an image"

    store = ImageStore(fetch, ImageProtocol.HALFBLOCK)
    key = ImageKey("https://cdn.discordapp.com/bad.png", 4, 4)
    store.get(key)
    await asyncio.wait_for(store.settled(), 5)
    assert store.failed(key)
    assert store.get(key) is None
    assert attempts == 1


async def test_kitty_store_uploads_each_animation_frame() -> None:
    written: list[str] = []
    ready = asyncio.Event()

    async def fetch(url: str) -> bytes:
        return gif_bytes(3)

    store = ImageStore(
        fetch, ImageProtocol.KITTY, write=written.append, on_ready=lambda _url: ready.set()
    )
    key = ImageKey("https://cdn.discordapp.com/a.gif", 4, 2, animate=True, exact=True)
    store.get(key)
    await asyncio.wait_for(ready.wait(), 5)
    rendered = store.get(key)
    assert rendered is not None
    assert rendered.animated
    assert len({frame.text.style for frame in rendered.frames}) == 3  # one image id per frame
    assert sum(sequence.count("a=T") for sequence in written) == 3
    store.clear()
    assert sum(sequence.count("a=d") for sequence in written) == 3


async def test_cancelled_load_does_not_unregister_its_replacement() -> None:
    fetches = 0
    release = asyncio.Event()

    async def fetch(url: str) -> bytes:
        nonlocal fetches
        fetches += 1
        await release.wait()
        return png_bytes(4, 4, (1, 2, 3, 255))

    store = ImageStore(fetch, ImageProtocol.HALFBLOCK)
    key = ImageKey("https://cdn.discordapp.com/a.png", 4, 2)
    store.get(key)
    await asyncio.sleep(0)
    store.clear()  # e.g. protocol change while loading
    store.get(key)  # the replacement load
    for _ in range(3):
        await asyncio.sleep(0)  # let the cancelled load unwind
    store.get(key)
    assert fetches == 2
    release.set()
    await store.settled()
    assert store.get(key) is not None


def test_decode_shrinks_every_frame_to_the_limit() -> None:
    size, frames = decode(gif_bytes(3, side=400), animate=True, limit=(40, 20))
    assert size == (400, 400)
    assert len(frames) == 3
    assert all(image.width <= 40 and image.height <= 20 for image, _ in frames)


async def test_off_protocol_never_fetches() -> None:
    calls: list[str] = []

    async def fetch(url: str) -> bytes:
        calls.append(url)
        return png_bytes(4, 4, (1, 2, 3, 255))

    store = ImageStore(fetch, ImageProtocol.OFF)
    assert store.get(ImageKey("https://cdn.discordapp.com/a.png", 4, 4)) is None
    await store.settled()
    assert calls == []


async def test_transient_failures_retry_after_a_backoff() -> None:
    attempts = 0

    async def fetch(url: str) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise NetworkError("offline")
        return png_bytes(4, 4, (1, 2, 3, 255))

    store = ImageStore(fetch, ImageProtocol.HALFBLOCK)
    key = ImageKey("https://cdn.discordapp.com/a.png", 4, 2)
    loop = asyncio.get_running_loop()
    store.get(key)
    await store.settled()
    assert not store.failed(key)
    assert store.get(key) is None  # still backing off: no new request
    assert attempts == 1
    store._retry_at[key] = loop.time()  # the backoff elapsed
    store.get(key)
    await store.settled()
    assert store.get(key) is not None
    assert attempts == 2


@pytest.mark.parametrize("error", [NotFoundError("gone"), InvalidInputError("media too large")])
async def test_permanent_fetch_failures_are_not_retried(error: Exception) -> None:
    async def fetch(url: str) -> bytes:
        raise error

    store = ImageStore(fetch, ImageProtocol.HALFBLOCK)
    key = ImageKey("https://cdn.discordapp.com/a.png", 4, 2)
    store.get(key)
    await store.settled()
    assert store.failed(key)


def test_oversized_canvas_is_refused_before_decoding() -> None:
    with pytest.raises(ValueError, match="too large"):
        decode(png_bytes(5000, 4000, (0, 0, 0, 255)), animate=False, limit=(10, 10))


async def test_decoding_is_bounded() -> None:
    running = peak = 0
    original = ImageStore._prepare

    def prepare(self: ImageStore, key: ImageKey, data: bytes) -> object:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            time.sleep(0.05)
            return original(self, key, data)
        finally:
            running -= 1

    async def fetch(url: str) -> bytes:
        return png_bytes(4, 4, (1, 2, 3, 255))

    store = ImageStore(fetch, ImageProtocol.HALFBLOCK)
    store._prepare = types.MethodType(prepare, store)  # type: ignore[method-assign]
    for index in range(6):
        store.get(ImageKey(f"https://cdn.discordapp.com/{index}.png", 4, 2))
    await store.settled()
    assert peak == DECODERS


async def test_kitty_images_beyond_the_budget_are_deleted_and_reported() -> None:
    written: list[str] = []
    evicted: list[set[str]] = []
    fetches: list[str] = []

    async def fetch(url: str) -> bytes:
        fetches.append(url)
        return png_bytes(4, 4, (1, 2, 3, 255))

    one_image = 4 * KITTY_CELL_PX[0] * 2 * KITTY_CELL_PX[1] * 4  # 4x2 cells, RGBA
    store = ImageStore(
        fetch,
        ImageProtocol.KITTY,
        write=written.append,
        on_evicted=evicted.append,
        terminal_budget=one_image,
    )
    first = ImageKey("https://cdn.discordapp.com/a.png", 4, 2, exact=True)
    second = ImageKey("https://cdn.discordapp.com/b.png", 4, 2, exact=True)
    store.get(first)
    await store.settled()
    store.get(second)
    await store.settled()
    assert evicted == [{first.url}]
    assert sum(sequence.count("a=d") for sequence in written) == 1
    assert store.get(first, load=False) is None  # gone, and not reloaded on its own
    await store.settled()
    assert fetches == [first.url, second.url]
    assert store.get(second) is not None
