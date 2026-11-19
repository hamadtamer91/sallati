"""Generic Shopify storefront adapter (public /products.json). Config-only stores."""
from __future__ import annotations

from typing import AsyncIterator

from pydantic import Field

from ..core.models import Currency, RawListing
from ._util import clean, pluck
from .base import AdapterConfig, Capabilities, StoreAdapter
from .registry import register_adapter


class ShopifyConfig(AdapterConfig):
    base_url: str
    collection: str | None = None
    currency: Currency = Currency.USD
    page_size: int = Field(250, ge=1, le=250)
    max_pages: int = Field(200, ge=1)
    brand_from_vendor: bool = True
    skip_product_types: list[str] = []


@register_adapter("shopify")
class ShopifyAdapter(StoreAdapter):
    capabilities = Capabilities(catalog=True)
    Config = ShopifyConfig
    cfg: ShopifyConfig

    async def iter_listings(self) -> AsyncIterator[RawListing]:
        base = self.cfg.base_url.rstrip("/")
        path = f"/collections/{self.cfg.collection}/products.json" if self.cfg.collection else "/products.json"
        skip = {s.lower() for s in self.cfg.skip_product_types}
        for page in range(1, self.cfg.max_pages + 1):
            data = await self.http.get_json(f"{base}{path}", params={"limit": self.cfg.page_size, "page": page})
            products = data.get("products") or []
            if not products:
                return
            for p in products:
                if (p.get("product_type") or "").lower() in skip:
                    continue
                for v in p.get("variants") or []:
                    variant = clean(v.get("title"))
                    has_variant = variant and variant.lower() != "default title"
                    yield RawListing(
                        external_id=str(v["id"]),
                        name=f"{clean(p['title'])} {variant}" if has_variant else clean(p["title"]),
                        price=v.get("price"),
                        currency=self.cfg.currency,
                        regular_price=v.get("compare_at_price"),
                        brand=clean(p.get("vendor")) if self.cfg.brand_from_vendor else None,
                        size_text=variant if has_variant else None,
                        gtin=v.get("barcode"),
                        in_stock=bool(v.get("available", True)),
                        url=f"{base}/products/{p.get('handle')}",
                        image_url=pluck(p, "images.0.src"),
                        category=clean(p.get("product_type")),
                    )
