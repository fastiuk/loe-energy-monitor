"""Decoding schedule images from sources: untrusted input, so PNG only and tightly bounded.

Pillow does the decoding: it is the maintained standard, ships wheels for every platform we
run on, and lets us restrict decoding to the PNG plugin and check the dimensions in the
header before any pixel data is decompressed.
"""

from __future__ import annotations

import io

from PIL import Image

from .base import fail

#: A real schedule PNG is about 130 KB.
MAX_BYTES = 2 * 1024 * 1024
#: The largest image any source publishes (Volyn's 1750×2250 is 3.9 MP).
MAX_PIXELS = 4_000_000

# Pillow's limits (MAX_IMAGE_PIXELS, PngImagePlugin.MAX_TEXT_*) are process-wide; this library
# also runs inside Home Assistant, so it leaves them alone and checks each image itself: the
# exact expected size is read from the header before any pixel data is decompressed.


def decode_png(data: bytes, size: tuple[int, int], what: str) -> Image.Image:
    """RGB pixels of a PNG that must be exactly ``size``; ``SourceFormatError`` otherwise."""
    if len(data) > MAX_BYTES:
        fail(f"{what}: image is {len(data)} bytes, over {MAX_BYTES}")
    try:
        image = Image.open(io.BytesIO(data), formats=["PNG"])
        # Only the header has been read so far: check it before inflating pixel data.
        if image.size != size or size[0] * size[1] > MAX_PIXELS:
            fail(f"{what}: image is {image.size[0]}×{image.size[1]}, expected {size[0]}×{size[1]}")
        return image.convert("RGB")
    except Image.DecompressionBombError as err:
        fail(f"{what}: image is too large ({err})")
    except (OSError, SyntaxError, ValueError, EOFError) as err:
        # Pillow reports corrupt or foreign data as any of these.
        fail(f"{what}: not a readable PNG ({type(err).__name__}: {err})")
