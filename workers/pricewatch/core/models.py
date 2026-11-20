"""Data contracts shared by adapters, the pipeline and storage.

Adapters only ever produce Raw* objects. Everything after that (normalization,
matching, persistence) is the core's job, so a new store never needs core changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Currency(str, Enum):
    USD = "USD"
    LBP = "LBP"


class BaseUnit(str, Enum):
    G = "g"
    ML = "ml"
    PIECE = "piece"


PromoType = Literal["price_cut", "percent_off", "multibuy", "bogo"]


class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# Approximate Lebanese coastline (lat -> westernmost land longitude), with ~3 km margin.
_COAST = [(33.05, 35.08), (33.27, 35.17), (33.56, 35.33), (33.90, 35.44), (34.10, 35.60),
          (34.30, 35.63), (34.45, 35.78), (34.70, 35.93)]


def coast_min_lng(lat: float) -> float:
    pts = _COAST
    if lat <= pts[0][0]:
        return pts[0][1]
    for (la1, lo1), (la2, lo2) in zip(pts, pts[1:]):
        if lat <= la2:
            return lo1 + (lo2 - lo1) * (lat - la1) / (la2 - la1)
    return pts[-1][1]


class RawBranch(_Raw):
    external_id: str
    name_en: str
    name_ar: str | None = None
    lat: float
    lng: float
    address: str | None = None
    region_slug: str | None = None
    opening_hours: dict[str, Any] | None = None

    @field_validator("lat")
    @classmethod
    def _lat(cls, v: float) -> float:
        if not 33.0 <= v <= 34.8:
            raise ValueError("latitude outside Lebanon")
        return v

    @field_validator("lng")
    @classmethod
    def _lng(cls, v: float, info) -> float:
        if not 35.0 <= v <= 36.7:
            raise ValueError("longitude outside Lebanon")
        lat = info.data.get("lat")
        if lat is not None and v < coast_min_lng(lat):
            raise ValueError("point is in the sea, west of the Lebanese coast")
        return v


class RawListing(_Raw):
    """One product as published by a store (website, app API or HTML page)."""

    external_id: str
    name: str = Field(min_length=2)
    price: Decimal | float | int | str
    currency: Currency | None = None          # None -> inferred from the price text/magnitude
    regular_price: Decimal | float | int | str | None = None
    brand: str | None = None
    size_text: str | None = None               # "6 x 330ml", "1.5 ل", "500 غ"
    gtin: str | None = None
    in_stock: bool = True
    url: str | None = None
    image_url: str | None = None
    category: str | None = None
    branch_external_id: str | None = None      # None = chain-wide / online price
    extra: dict[str, Any] = Field(default_factory=dict)


class RawPromotion(_Raw):
    """One deal read from a flyer/catalogue page by the vision model."""

    name: str
    brand: str | None = None
    size_text: str | None = None
    price: Decimal | float | int | str | None = None
    regular_price: Decimal | float | int | str | None = None
    currency: Currency | None = None
    promo_type: PromoType = "price_cut"
    percent_off: float | None = None
    buy_qty: int | None = None
    get_qty: int | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    confidence: float = 0.5
    page: int | None = None
    gtin: str | None = None


class FlyerSource(_Raw):
    url: str
    title: str | None = None
    mime_type: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None


@dataclass(frozen=True)
class ParsedQuantity:
    unit_size: Decimal          # size of ONE item, in base unit
    base_unit: BaseUnit
    pack_count: int = 1
    sold_by_weight: bool = False
    matched_text: str = ""

    @property
    def total(self) -> Decimal:
        return self.unit_size * self.pack_count


@dataclass
class ProductIdentity:
    """What the matcher needs to know about a product (from a listing or a flyer item)."""
    name_norm: str
    name_core: str
    brand_norm: str
    quantity: ParsedQuantity | None
    gtin: str | None


@dataclass
class NormalizedListing:
    raw: RawListing
    name_norm: str
    name_core: str              # name without brand/size tokens, used for fuzzy matching
    brand_norm: str
    quantity: ParsedQuantity | None
    price: Decimal
    currency: Currency
    price_usd: Decimal
    regular_price_usd: Decimal | None
    unit_price_usd: Decimal | None
    unit_price_basis: Literal["kg", "l", "piece"] | None
    gtin: str | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class StoreRecord:
    id: str
    slug: str
    name_en: str
    name_ar: str
    adapter_key: str
    adapter_config: dict[str, Any]
    rate_limit_rps: float = 0.5
    pricing_scope: str = "chain"
    is_active: bool = True
