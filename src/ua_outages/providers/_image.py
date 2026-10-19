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


def decode_png(data: bytes, size: tuple[int, int | range], what: str) -> Image.Image:
    """RGB pixels of a PNG of exactly ``size`` (the height may be a range); ``SourceFormatError`` otherwise."""
    if len(data) > MAX_BYTES:
        fail(f"{what}: image is {len(data)} bytes, over {MAX_BYTES}")
    width, heights = size
    tallest = heights if isinstance(heights, int) else max(heights)
    try:
        image = Image.open(io.BytesIO(data), formats=["PNG"])
        # Only the header has been read so far: check it before inflating pixel data.
        height_ok = image.size[1] == heights if isinstance(heights, int) else image.size[1] in heights
        if image.size[0] != width or not height_ok or width * tallest > MAX_PIXELS:
            expected = f"{width}×{heights}" if isinstance(heights, int) else f"{width}×{min(heights)}–{tallest}"
            fail(f"{what}: image is {image.size[0]}×{image.size[1]}, expected {expected}")
        if image.mode in ("RGBA", "LA", "P"):
            # Transparent pixels read as the white page they are shown on.
            page = Image.new("RGB", image.size, (255, 255, 255))
            page.paste(image, mask=image.convert("RGBA").getchannel("A"))
            return page
        return image.convert("RGB")
    except Image.DecompressionBombError as err:
        fail(f"{what}: image is too large ({err})")
    except (OSError, SyntaxError, ValueError, EOFError) as err:
        # Pillow reports corrupt or foreign data as any of these.
        fail(f"{what}: not a readable PNG ({type(err).__name__}: {err})")
