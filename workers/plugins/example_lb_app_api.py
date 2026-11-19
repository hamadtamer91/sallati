"""Example of a store-specific adapter living OUTSIDE the core package.

Loaded automatically when PRICEWATCH_PLUGIN_PATHS includes this folder (the GitHub
workflow and Dockerfile set it). Copy this file, rename the key, adapt the parsing.

This one models a typical delivery-app web API: zones instead of branches, prices in
LBP as integers, sizes hidden in a "variant" object, pagination by cursor.
"""
from __future__ import annotations

from typing import AsyncIterator

from pricewatch.adapters.base import AdapterConfig, Capabilities, StoreAdapter
from pricewatch.adapters.registry import register_adapter
from pricewatch.core.models import Currency, RawBranch, RawListing


class AppConfig(AdapterConfig):
    api_base: str
    zone_ids: list[str]
    page_size: int = 50


@register_adapter("example_lb_app_api")
class ExampleLbAppAdapter(StoreAdapter):
    capabilities = Capabilities(catalog=True, branches=True, branch_pricing=True)
    Config = AppConfig
    cfg: AppConfig

    async def fetch_branches(self) -> list[RawBranch]:
        data = await self.http.get_json(f"{self.cfg.api_base}/zones")
        return [RawBranch(external_id=z["id"], name_en=z["label"], lat=z["center"][0], lng=z["center"][1])
                for z in data["zones"] if z["id"] in self.cfg.zone_ids]

    async def iter_listings(self) -> AsyncIterator[RawListing]:
        for zone in self.cfg.zone_ids:
            cursor = None
            while True:
                params = {"zone": zone, "limit": self.cfg.page_size, **({"after": cursor} if cursor else {})}
                page = await self.http.get_json(f"{self.cfg.api_base}/catalog", params=params)
                for p in page["products"]:
                    v = p.get("variant") or {}
                    yield RawListing(
                        external_id=str(p["sku"]),
                        name=p["name_ar"] or p["name_en"],
                        brand=p.get("brand"),
                        size_text=f'{v.get("qty", "")} {v.get("unit", "")}'.strip() or None,
                        price=p["price_lbp"],
                        currency=Currency.LBP,
                        regular_price=p.get("was_lbp"),
                        gtin=p.get("ean"),
                        in_stock=p.get("stock", 0) > 0,
                        branch_external_id=zone,
                    )
                cursor = page.get("next")
                if not cursor:
                    break
