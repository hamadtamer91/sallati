"""Generic WooCommerce adapter using the public Store API (/wp-json/wc/store/v1/products)."""
from __future__ import annotations

from typing import AsyncIterator

from pydantic import Field

from ..core.models import Currency, RawListing
from ._util import clean, minor_units, pluck
from .base import AdapterConfig, Capabilities, StoreAdapter
from .registry import register_adapter


class WooConfig(AdapterConfig):
    base_url: str
    per_page: int = Field(100, ge=1, le=100)
    max_pages: int = Field(500, ge=1)
    category: str | None = None           # category id or slug filter
    currency_override: Currency | None = None
    brand_attribute: str | None = "brand"  # attribute name holding the brand, if any


@register_adapter("woocommerce_store_api")
class WooCommerceAdapter(StoreAdapter):
    capabilities = Capabilities(catalog=True)
    Config = WooConfig
    cfg: WooConfig

    def _brand(self, p: dict) -> str | None:
        for b in p.get("brands") or []:
            return clean(b.get("name"))
        if self.cfg.brand_attribute:
            for a in p.get("attributes") or []:
                if (a.get("name") or "").lower() == self.cfg.brand_attribute.lower():
                    return clean(pluck(a, "terms.0.name"))
        return None

    async def iter_listings(self) -> AsyncIterator[RawListing]:
        base = self.cfg.base_url.rstrip("/")
        url = f"{base}/wp-json/wc/store/v1/products"
        for page in range(1, self.cfg.max_pages + 1):
            params: dict = {"per_page": self.cfg.per_page, "page": page}
            if self.cfg.category:
                params["category"] = self.cfg.category
            resp = await self.http.request("GET", url, params=params)
            items = resp.json() or []
            for p in items:
                prices = p.get("prices") or {}
                minor = int(prices.get("currency_minor_unit", 2))
                currency = self.cfg.currency_override or (
                    Currency(prices["currency_code"]) if prices.get("currency_code") in ("USD", "LBP") else None)
                yield RawListing(
                    external_id=str(p["id"]),
                    name=clean(p.get("name")) or "",
                    price=minor_units(prices.get("price"), minor),
                    currency=currency,
                    regular_price=minor_units(prices.get("regular_price"), minor),
                    brand=self._brand(p),
                    gtin=p.get("sku") if (p.get("sku") or "").isdigit() else None,
                    in_stock=bool(p.get("is_in_stock", True)),
                    url=p.get("permalink"),
                    image_url=pluck(p, "images.0.src"),
                    category=clean(pluck(p, "categories.0.name")),
                )
            total_pages = int(resp.headers.get("X-WP-TotalPages", page if not items else page + 1))
            if not items or page >= total_pages:
                return
