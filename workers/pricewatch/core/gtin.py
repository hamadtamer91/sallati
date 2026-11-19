"""Barcode (EAN-8 / UPC-A / EAN-13 / GTIN-14) normalization to GTIN-14."""
from __future__ import annotations

import re

from .text import to_ascii_digits


def _check_digit(body: str) -> int:
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def normalize_gtin(value: str | int | None) -> str | None:
    """Return a 14-digit GTIN if the code is valid, else None (bad codes are ignored, not trusted)."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", to_ascii_digits(str(value)))
    if len(digits) not in (8, 12, 13, 14):
        return None
    gtin = digits.zfill(14)
    if set(gtin) == {"0"} or _check_digit(gtin[:-1]) != int(gtin[-1]):
        return None
    return gtin
