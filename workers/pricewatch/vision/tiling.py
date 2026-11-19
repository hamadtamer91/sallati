"""Render flyers (PDF or images) to pages and split large pages into overlapping tiles."""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image

Image.MAX_IMAGE_PIXELS = 120_000_000  # big flyers are normal; still guard against bombs


@dataclass
class Tile:
    page: int
    row: int
    col: int
    box: tuple[int, int, int, int]
    image: Image.Image

    @property
    def label(self) -> str:
        return f"row {self.row + 1}, column {self.col + 1}"

    def jpeg(self, quality: int = 85) -> bytes:
        buf = io.BytesIO()
        self.image.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()


def is_pdf(content: bytes, mime: str | None = None) -> bool:
    return content[:5] == b"%PDF-" or (mime or "").lower().endswith("pdf")


def render_document(content: bytes, mime: str | None = None, dpi: int = 170, max_pages: int = 40) -> list[Image.Image]:
    if is_pdf(content, mime):
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(content)
        try:
            return [pdf[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(min(len(pdf), max_pages))]
        finally:
            pdf.close()
    img = Image.open(io.BytesIO(content))
    img.load()
    return [img.convert("RGB")]


def _starts(length: int, tile: int, overlap: float) -> list[int]:
    if length <= tile:
        return [0]
    step = tile * (1 - overlap)
    n = math.ceil((length - tile) / step) + 1
    return [round(i * (length - tile) / (n - 1)) for i in range(n)]


def make_tiles(img: Image.Image, page: int = 1, tile_px: int = 1100, overlap: float = 0.15,
               max_side: int = 1568) -> list[Tile]:
    """Overlapping grid so every product is fully inside at least one tile.

    tile_px is measured on the page as rendered; small text stays legible because each tile is
    sent close to native resolution (capped at max_side, the model's preferred long edge)."""
    w, h = img.size
    tw, th = min(tile_px, w), min(tile_px, h)
    tiles: list[Tile] = []
    for r, y in enumerate(_starts(h, th, overlap)):
        for c, x in enumerate(_starts(w, tw, overlap)):
            box = (x, y, x + tw, y + th)
            crop = img.crop(box)
            if max(crop.size) > max_side:
                crop.thumbnail((max_side, max_side))
            tiles.append(Tile(page, r, c, box, crop))
    return tiles


def overview(img: Image.Image, max_side: int = 1400) -> bytes:
    im = img.copy()
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80)
    return buf.getvalue()
