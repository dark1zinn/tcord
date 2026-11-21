import asyncio
import io

import pytest
from PIL import Image
from rich.cells import cell_len
from rich.style import Style
from tcord._diacritics import DIACRITICS
from tcord.images import (
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


def test_explicit_setting_overrides_detection() -> None:
    assert resolve_protocol("halfblock", {"TERM": "xterm-kitty"}) is ImageProtocol.HALFBLOCK
    assert resolve_protocol("bogus", {"TERM": "xterm-kitty"}) is ImageProtocol.KITTY


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


def test_off_protocol_never_fetches() -> None:
    async def fetch(url: str) -> bytes:
        raise AssertionError("fetched")

    store = ImageStore(fetch, ImageProtocol.OFF)
    assert store.get(ImageKey("https://cdn.discordapp.com/a.png", 4, 4)) is None
