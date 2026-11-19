"""SKU matching: source listing -> canonical product.

Order of evidence:
  1. Barcode (GTIN-14)                                   -> confidence 1.0
  2. Same brand + same base unit + same pack count + size within 2%,
     then fuzzy name similarity on the "core" name        -> confidence = similarity
  3. Nothing close enough -> create a new canonical product
A listing whose quantity cannot be parsed is never auto-matched (it could be a carton
vs. a single piece); it is stored for review instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Protocol

from rapidfuzz import fuzz

from .models import BaseUnit, NormalizedListing, ParsedQuantity, ProductIdentity
from .units import same_quantity

MatchMethod = Literal["barcode", "attributes", "fuzzy", "new", "none"]


@dataclass(frozen=True)
class CanonicalCandidate:
    id: str
    name_core: str
    brand_norm: str
    base_unit: BaseUnit
    unit_size: Decimal
    pack_count: int
    gtin: str | None = None

    @property
    def quantity(self) -> ParsedQuantity:
        return ParsedQuantity(self.unit_size, self.base_unit, self.pack_count)


class CatalogIndex(Protocol):
    def by_gtin(self, gtin: str) -> CanonicalCandidate | None: ...

    def candidates(self, brand_norm: str, base_unit: BaseUnit, pack_count: int,
                   unit_size: Decimal, tolerance: float) -> list[CanonicalCandidate]: ...


@dataclass
class MatchResult:
    canonical_id: str | None
    method: MatchMethod
    confidence: float
    needs_review: bool = False
    review_reason: str | None = None
    create_new: bool = False
    alternatives: list[tuple[str, float]] = field(default_factory=list)


def name_score(a: str, b: str) -> float:
    """0-100. Both names reduced to nothing but brand+size -> same product.
    Average of set & sort ratios so 'milk' vs 'chocolate milk' is NOT a perfect match."""
    if not a and not b:
        return 100.0
    if not a or not b:
        return 0.0
    return (fuzz.token_set_ratio(a, b) + fuzz.token_sort_ratio(a, b)) / 2


class ProductMatcher:
    def __init__(self, index: CatalogIndex, accept: float = 88.0, review: float = 72.0,
                 no_brand_accept: float = 93.0, size_tolerance: float = 0.02):
        self.index = index
        self.accept = accept
        self.review = review
        self.no_brand_accept = no_brand_accept
        self.size_tolerance = size_tolerance

    def match(self, n: NormalizedListing | ProductIdentity, allow_create: bool = True) -> MatchResult:
        if n.gtin:
            c = self.index.by_gtin(n.gtin)
            if c:
                if n.quantity and not same_quantity(n.quantity, c.quantity, 0.05):
                    return MatchResult(c.id, "barcode", 0.9, True, "barcode_size_mismatch")
                return MatchResult(c.id, "barcode", 1.0)

        if n.quantity is None:
            return MatchResult(None, "none", 0.0, True, "quantity_unparsed")

        q = n.quantity
        cands = self.index.candidates(n.brand_norm, q.base_unit, q.pack_count, q.unit_size, self.size_tolerance)
        cands = [c for c in cands if same_quantity(q, c.quantity, self.size_tolerance)]
        scored = sorted(
            ((c, name_score(n.name_core, c.name_core)) for c in cands),
            key=lambda x: x[1], reverse=True,
        )
        accept = self.accept if n.brand_norm else self.no_brand_accept
        alts = [(c.id, s) for c, s in scored[:3]]
        if scored:
            best, score = scored[0]
            # Barcode conflict: both have different valid barcodes -> different products.
            if n.gtin and best.gtin and best.gtin != n.gtin:
                scored = [(c, s) for c, s in scored if not c.gtin or c.gtin == n.gtin]
                if not scored:
                    return MatchResult(None, "new", 1.0, create_new=allow_create, alternatives=alts)
                best, score = scored[0]
            if score >= accept:
                return MatchResult(best.id, "attributes", round(score / 100, 3), alternatives=alts)
            if score >= self.review:
                return MatchResult(best.id, "fuzzy", round(score / 100, 3), True, "low_name_similarity", alternatives=alts)
        if not n.brand_norm:
            # no brand + no close match: create, but let a human confirm
            return MatchResult(None, "new", 0.5, True, "no_brand", create_new=allow_create, alternatives=alts)
        return MatchResult(None, "new", 1.0, create_new=allow_create, alternatives=alts)
