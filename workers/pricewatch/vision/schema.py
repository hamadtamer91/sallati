"""Strict JSON Schema sent to the vision model + matching Pydantic models used to re-validate."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _nullable(t: dict) -> dict:
    return {"anyOf": [t, {"type": "null"}]}


_STR = {"type": "string"}
_NUM = {"type": "number"}
_INT = {"type": "integer"}

ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "name": {**_STR, "description": "Product name as printed, Arabic or English. No price in it."},
        "brand": _nullable(_STR),
        "size_text": _nullable({**_STR, "description": "Size/weight/count as printed, e.g. '6 x 330ml', '1 كغ', '30 حبة'"}),
        "price": _nullable({**_NUM, "description": "Price paid in the offer (bundle price for 'N for X')"}),
        "regular_price": _nullable({**_NUM, "description": "Old/strikethrough price if printed"}),
        "currency": _nullable({"type": "string", "enum": ["USD", "LBP"]}),
        "promo_type": {"type": "string", "enum": ["price_cut", "percent_off", "multibuy", "bogo"]},
        "percent_off": _nullable(_NUM),
        "buy_qty": _nullable({**_INT, "description": "multibuy: N in 'N for X'; bogo: N in 'buy N get M'"}),
        "get_qty": _nullable({**_INT, "description": "bogo: M free items"}),
        "valid_from": _nullable({**_STR, "description": "YYYY-MM-DD, only if printed for this item"}),
        "valid_until": _nullable({**_STR, "description": "YYYY-MM-DD, only if printed for this item"}),
        "confidence": {**_NUM, "description": "0 to 1: certainty that this price belongs to this product"},
    },
    "required": ["name", "brand", "size_text", "price", "regular_price", "currency", "promo_type",
                 "percent_off", "buy_qty", "get_qty", "valid_from", "valid_until", "confidence"],
    "additionalProperties": False,
}

TILE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "flyer_valid_from": _nullable({**_STR, "description": "YYYY-MM-DD if the offer period is printed on this part"}),
        "flyer_valid_until": _nullable(_STR),
        "items": {"type": "array", "items": ITEM_SCHEMA},
    },
    "required": ["flyer_valid_from", "flyer_valid_until", "items"],
    "additionalProperties": False,
}

HEADER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "store_name": _nullable(_STR),
        "flyer_valid_from": _nullable(_STR),
        "flyer_valid_until": _nullable(_STR),
        "default_currency": _nullable({"type": "string", "enum": ["USD", "LBP"]}),
    },
    "required": ["store_name", "flyer_valid_from", "flyer_valid_until", "default_currency"],
    "additionalProperties": False,
}


class FlyerItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=2)
    brand: str | None = None
    size_text: str | None = None
    price: float | None = None
    regular_price: float | None = None
    currency: Literal["USD", "LBP"] | None = None
    promo_type: Literal["price_cut", "percent_off", "multibuy", "bogo"] = "price_cut"
    percent_off: float | None = None
    buy_qty: int | None = None
    get_qty: int | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    confidence: float = Field(0.5, ge=0, le=1)


class TileExtraction(BaseModel):
    flyer_valid_from: str | None = None
    flyer_valid_until: str | None = None
    items: list[FlyerItem] = []


class FlyerHeader(BaseModel):
    store_name: str | None = None
    flyer_valid_from: str | None = None
    flyer_valid_until: str | None = None
    default_currency: Literal["USD", "LBP"] | None = None


def tile_instruction(today: str, page: int, tile: str, store_name: str) -> str:
    return f"""You are reading part ({tile}) of page {page} of a weekly offers flyer from the Lebanese supermarket "{store_name}". Today is {today}.
Extract every product that has a clearly visible price in THIS image. Rules:
- Only include an item if both its name and its price are fully visible. Skip items cut off at the image edge.
- Never invent values. Use null for anything not printed.
- Prices: "$", "USD", "دولار" = USD; "ل.ل", "LBP", "L.L" = LBP. Write numbers without separators (178000, 2.99).
- Strikethrough / "was" / "بدل" price -> regular_price.
- "3 for $5" / "3 بـ 5$" -> promo_type multibuy, buy_qty 3, price 5.
- "2+1", "buy 2 get 1 free", "1+1 مجاناً" -> promo_type bogo, buy_qty 2, get_qty 1, price = unit price if printed.
- "-20%" / "خصم 20%" -> promo_type percent_off, percent_off 20.
- Otherwise promo_type price_cut.
- Dates: output YYYY-MM-DD. Lebanese dates are day/month (12/11 = 12 November). Infer the year from today.
- Keep Arabic names in Arabic. size_text exactly as printed (e.g. "6 x 330 مل").
- confidence below 0.6 when the price could belong to a neighbouring product."""


def header_instruction(today: str) -> str:
    return (f"This is the first page of a Lebanese supermarket offers flyer. Today is {today}. "
            "Return the store name, the offer validity period printed on it (YYYY-MM-DD; Lebanese dates "
            "are day/month), and the currency most prices use. Use null when not printed.")
