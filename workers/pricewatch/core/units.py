"""Quantity parsing (Arabic + English) and unit-price normalization.

Everything is reduced to one of three base units: grams, millilitres, pieces.
A pack is unit_size x pack_count, so "6 x 330ml" never compares with "330ml".
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from .models import BaseUnit, ParsedQuantity
from .text import to_ascii_digits

# token -> (base unit, factor to base unit). Longest tokens are tried first.
_UNITS: dict[str, tuple[BaseUnit, Decimal]] = {}


def _add(tokens: str, unit: BaseUnit, factor: str) -> None:
    for t in tokens.split("|"):
        _UNITS[t] = (unit, Decimal(factor))


_add("g|gr|grs|gm|gms|gram|grams|gramme|grammes|غ|غم|غرام|غرامات|جم|جرام|ج|غر", BaseUnit.G, "1")
_add("kg|kgs|kilo|kilos|kilogram|kilograms|كغ|كلغ|كغم|كيلو|كيلوغرام|كجم|كلغم|كيلوجرام", BaseUnit.G, "1000")
_add("mg|ملغ", BaseUnit.G, "0.001")
_add("lb|lbs", BaseUnit.G, "453.592")
_add("oz", BaseUnit.G, "28.3495")
_add("ml|mls|millilitre|milliliter|مل|ملل|مللتر|ميليلتر|مليلتر|ملم", BaseUnit.ML, "1")
_add("cl", BaseUnit.ML, "10")
_add("l|lt|ltr|ltrs|litre|litres|liter|liters|ل|لتر|ليتر|لترات|ليترات", BaseUnit.ML, "1000")
_add("fl oz|floz", BaseUnit.ML, "29.5735")
_add(
    "pc|pcs|piece|pieces|pce|unit|units|roll|rolls|sachet|sachets|bag|bags|tab|tabs|tablets|"
    "capsules|eggs|egg|sheets|wipes|count|ct|"
    "حبة|حبه|حبات|قطعة|قطعه|قطع|رول|رولات|كيس|اكياس|ظرف|اظرف|بيضة|بيضه|بيضات|منديل|محرمة|محارم|ورقة",
    BaseUnit.PIECE,
    "1",
)

_UNIT_ALT = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
_NUM = r"\d+(?:[.,]\d+)?"
# after the unit: not followed by a letter (so "ل" doesn't eat "لبن") nor by ".ل" (= ل.ل currency)
_END = r"(?![^\W\d_])(?!\.\s?ل)"
_X = r"\s*[x×*]\s*"

_RE_PACK_FIRST = re.compile(rf"(?P<count>\d{{1,3}}){_X}(?P<num>{_NUM})\s*(?P<unit>{_UNIT_ALT}){_END}", re.I)
_RE_SIZE_FIRST = re.compile(rf"(?P<num>{_NUM})\s*(?P<unit>{_UNIT_ALT}){_END}{_X}(?P<count>\d{{1,3}})(?!\d)", re.I)
_RE_SINGLE = re.compile(rf"(?P<num>{_NUM})\s*-?\s*(?P<unit>{_UNIT_ALT}){_END}", re.I)
_RE_PACK_WORD = re.compile(
    r"(?:pack\s*of|packs?\s*of|box\s*of|carton\s*of|case\s*of|عبوة|علبة|كرتونة|كرتونه|طرد|باكيت)\s*(?P<a>\d{1,3})"
    r"|(?P<b>\d{1,3})\s*-?\s*(?:pack|pk|packs|carton|حبات|عبوات|علب|قناني|زجاجات|tins|cans|bottles)(?![^\W\d_])",
    re.I,
)
_RE_BY_WEIGHT = re.compile(r"(?:/\s*kg|per\s*kg|per\s*kilo|بالكيلو|للكيلو|الكيلو|كيلو\s*واحد|1\s*kg\s*price)", re.I)


def _num(s: str) -> Decimal:
    """'1,5' -> 1.5 ; '1,000' -> 1000 ; '1.5' -> 1.5"""
    if "," in s:
        head, _, tail = s.partition(",")
        s = head + tail if len(tail) == 3 else f"{head}.{tail}"
    return Decimal(s)


def _q(value: Decimal) -> Decimal:
    q = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return q.quantize(Decimal(1)) if q == q.to_integral_value() else q.normalize()


_BARE_WEIGHT = re.compile(r"^\s*(?:per\s*)?(?:kg|kgs|kilo|كغ|كلغ|كيلو|بالكيلو)\.?\s*$", re.I)
_BARE_PIECE = re.compile(
    r"^\s*(?:pc|pcs|piece|pieces|each|unit|bag|box|bunch|pack|packet|tray|jar|bottle|can|roll|"
    r"حبة|قطعة|كيس|علبة|ربطة|باكيت)\.?\s*$", re.I)


def _bare_size(text: str) -> ParsedQuantity | None:
    """Size fields that are only a unit: "Kg" = sold by weight (price per kg), "BAG" = one item."""
    if _BARE_WEIGHT.match(text):
        return ParsedQuantity(Decimal("1000"), BaseUnit.G, 1, True, text.strip())
    if _BARE_PIECE.match(text):
        return ParsedQuantity(Decimal("1"), BaseUnit.PIECE, 1, False, text.strip())
    return None


def parse_quantity(*texts: str | None) -> ParsedQuantity | None:
    """Parse the first text that yields a quantity (e.g. size_text first, then the name)."""
    for i, text in enumerate(texts):
        if text:
            q = _parse_one(to_ascii_digits(text))
            if q:
                return q
            if i == 0:  # only a dedicated size field may be a bare unit
                bare = _bare_size(text)
                if bare:
                    return bare
    return None


def _parse_one(text: str) -> ParsedQuantity | None:
    t = text.lower()
    by_weight = bool(_RE_BY_WEIGHT.search(t))

    for rx in (_RE_PACK_FIRST, _RE_SIZE_FIRST):
        m = rx.search(t)
        if m:
            unit, factor = _UNITS[m.group("unit").lower()]
            count = int(m.group("count"))
            if count >= 1:
                return ParsedQuantity(_q(_num(m.group("num")) * factor), unit, count, False, m.group(0))

    m = _RE_SINGLE.search(t)
    if m:
        unit, factor = _UNITS[m.group("unit").lower()]
        size = _q(_num(m.group("num")) * factor)
        if size <= 0:
            return None
        pack = 1
        pm = _RE_PACK_WORD.search(t)
        if pm and unit is not BaseUnit.PIECE:
            pack = int(pm.group("a") or pm.group("b"))
        return ParsedQuantity(size, unit, max(pack, 1), by_weight, m.group(0))

    if by_weight:  # "Tomatoes /kg" with no explicit number
        return ParsedQuantity(Decimal("1000"), BaseUnit.G, 1, True, "kg")

    pm = _RE_PACK_WORD.search(t)
    if pm:  # "Eggs carton 30" -> 30 pieces
        return ParsedQuantity(Decimal(int(pm.group("a") or pm.group("b"))), BaseUnit.PIECE, 1, False, pm.group(0))
    return None


def unit_price(price_usd: Decimal, q: ParsedQuantity) -> tuple[Decimal, str]:
    """Price per kg / per litre / per piece."""
    if q.base_unit is BaseUnit.G:
        return (price_usd / (q.total / 1000)).quantize(Decimal("0.000001")), "kg"
    if q.base_unit is BaseUnit.ML:
        return (price_usd / (q.total / 1000)).quantize(Decimal("0.000001")), "l"
    return (price_usd / q.total).quantize(Decimal("0.000001")), "piece"


def same_quantity(a: ParsedQuantity, b: ParsedQuantity, tolerance: float = 0.02) -> bool:
    """Strict pack equality + size within tolerance (1L == 1000ml, 454g ~ 453.6g)."""
    if a.base_unit is not b.base_unit or a.pack_count != b.pack_count:
        return False
    hi = max(a.unit_size, b.unit_size)
    return hi > 0 and abs(a.unit_size - b.unit_size) / hi <= Decimal(str(tolerance))


def strip_quantities(text: str) -> str:
    """Remove every quantity expression from an (already normalized) name."""
    t = to_ascii_digits(text)
    for rx in (_RE_PACK_FIRST, _RE_SIZE_FIRST, _RE_SINGLE, _RE_PACK_WORD, _RE_BY_WEIGHT):
        t = rx.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()
