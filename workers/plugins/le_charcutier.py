"""Le Charcutier (lecharcutier.com) — store-specific adapter, kept outside the core.

How the site works (checked Nov 2026; robots.txt allows everything except /cgi-bin/):
  * /shop/{slug} pages carry a Laravel CSRF token and the category id (#cat_filter).
  * POST /shop_filter {category, offset, ...} returns 30 product cards at a time
    (brand, name, size, product id) plus total/current counts.
  * Prices are not in the cards: POST /get_product_details/{id}/{zone} returns the product
    popup with "LBP 531,024" and a struck-through old price ("LBP 0" = no discount).
  * POST /our_branches_ajax lists the 19 branches with Google-Maps embeds (…!2d<lng>!3d<lat>).
Prices are the same in every branch (chain pricing).
"""
from __future__ import annotations

import html as htmllib
import logging
import re
from typing import AsyncIterator

from pydantic import Field

from pricewatch.adapters.base import AdapterConfig, Capabilities, StoreAdapter
from pricewatch.adapters.registry import register_adapter
from pricewatch.core.models import Currency, RawBranch, RawListing

log = logging.getLogger(__name__)

CARD = re.compile(r'<div class="col-xs-4 category-box">(.*?)(?=<div class="col-xs-4 category-box">|\Z)', re.S)
CARD_ID = re.compile(r"product_details\('(\d+)','(\d+)'\)")
CARD_URL = re.compile(r'<a href="([^"]+)"\s+onclick="product_details')
CARD_IMG = re.compile(r"background-image:\s*url\('([^']+)'\)")
H4 = re.compile(r"<h4>\s*(.*?)\s*</h4>", re.S)
H5 = re.compile(r"<h5[^>]*>\s*(.*?)\s*</h5>", re.S)
H6 = re.compile(r"<h6>\s*(.*?)\s*</h6>", re.S)
PRICE = re.compile(r'<p class="price">\s*(?:LBP|L\.L\.?)\s*([\d,.]+)', re.I)
OLD_PRICE = re.compile(r"<strike>\s*(?:LBP|L\.L\.?)\s*([\d,.]+)\s*</strike>", re.I)
BRANCH = re.compile(
    r'<h3 class="pull-left">\s*([^<]+?)\s*</h3>.*?Area</li>\s*<li class="branch_light">\s*([^<]*?)\s*</li>'
    r'.*?!2d(-?[\d.]+)!3d(-?[\d.]+)', re.S)

# Branch area -> our region slug (seed.sql). Unknown areas are stored without a region.
REGION_HINTS = {
    "ashrafieh": "beirut", "achrafieh": "beirut", "baabda": "baabda", "chiah": "baabda",
    "jisr el basha": "metn", "saloumi": "metn", "dekwaneh": "metn", "sid el bauchrieh": "metn",
    "fanar": "metn", "monteverde": "metn", "beit mery": "metn", "antelias": "metn", "jal el dib": "metn",
    "naccash": "metn", "rabieh": "metn", "zouk": "kesrouan", "sarba": "kesrouan", "adonis": "kesrouan",
    "jbeil": "jbeil", "batroun": "batroun", "semkanieh": "chouf",
}

# Food & household departments (slugs from the site's "Shop All" menu). Edit in stores.yaml.
DEFAULT_DEPARTMENTS = [
    "bakery", "fruits-vegetables-pickles", "meat-poultry-fish", "deli-meat-cheese",
    "milk-dairy-products-eggs", "chilled-frozen", "grocery-foods", "sweets-snacks",
    "wellness-healthy-food", "cold-hot-beverages", "baby-world", "pet-world",
    "fresh-health-beauty", "593-paper-cleaning",
]


def _text(fragment: str | None) -> str | None:
    if not fragment:
        return None
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


class LeCharcutierConfig(AdapterConfig):
    base_url: str = "https://www.lecharcutier.com"
    departments: list[str] = Field(default_factory=lambda: list(DEFAULT_DEPARTMENTS))
    price_zone: int = 13            # the site's default price list ("dropdown_filter")
    max_products: int | None = None  # cap for dry runs
    include_variants: bool = False   # multi-variant products (sizes in one card) — not parsed yet


@register_adapter("le_charcutier")
class LeCharcutierAdapter(StoreAdapter):
    capabilities = Capabilities(catalog=True, branches=True, branch_pricing=False)
    Config = LeCharcutierConfig
    cfg: LeCharcutierConfig
    _token: str | None = None
    skipped_variants = 0
    skipped_no_price = 0

    # --- session ------------------------------------------------------------------
    async def _open(self, path: str) -> str:
        """GET a page; keeps cookies and refreshes the CSRF token used by the site's own scripts."""
        page = await self.http.get_text(f"{self.cfg.base_url}{path}")
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
        if m:
            self._token = m.group(1)
        return page

    async def _post(self, path: str, data: dict, referer: str) -> str:
        headers = {"X-CSRF-TOKEN": self._token or "", "X-Requested-With": "XMLHttpRequest", "Referer": referer}
        r = await self.http.request("POST", f"{self.cfg.base_url}{path}", data=data, headers=headers)
        return r.text

    # --- branches -----------------------------------------------------------------
    async def fetch_branches(self) -> list[RawBranch]:
        page_url = f"{self.cfg.base_url}/Our-Branches"
        await self._open("/Our-Branches")
        import json

        data = json.loads(await self._post("/our_branches_ajax", {"search": ""}, page_url))
        out: list[RawBranch] = []
        seen_coords: dict[tuple[float, float], str] = {}
        for name, area, lng, lat in BRANCH.findall(data.get("html", "")):
            name, area = htmllib.unescape(name), htmllib.unescape(area)
            ext = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("le charcutier", "")).strip("-")
            coords = (round(float(lat), 4), round(float(lng), 4))
            # The site has a few wrong pins (one in the sea, one copied from a neighbour branch).
            # Skip them rather than mislead the route planner; fix via stores.yaml `branch_overrides`.
            override = (self.ctx.config.get("branch_overrides") or {}).get(ext)
            if override:
                coords = (float(override["lat"]), float(override["lng"]))
            elif coords in seen_coords:
                log.warning("branch %s shares coordinates with %s — skipped until fixed", ext, seen_coords[coords])
                continue
            try:
                hint = next((v for k, v in REGION_HINTS.items() if k in f"{name} {area}".lower()), None)
                out.append(RawBranch(external_id=ext, name_en=name, address=area, lat=coords[0], lng=coords[1],
                                     region_slug=hint))
                seen_coords[coords] = ext
            except ValueError as e:  # outside Lebanon / in the sea
                log.warning("branch %s has invalid coordinates %s (%s) — skipped", ext, coords, e)
        return out

    # --- catalogue ----------------------------------------------------------------
    def parse_cards(self, fragment: str, department: str) -> list[dict]:
        cards = []
        for body in CARD.findall(fragment):
            m = CARD_ID.search(body)
            if not m:
                continue
            url = CARD_URL.search(body)
            img = CARD_IMG.search(body)
            cards.append({
                "id": m.group(1), "variant": m.group(2) != "0",
                "brand": _text((H4.search(body) or [None, None])[1] if H4.search(body) else None),
                "name": _text(H5.search(body).group(1)) if H5.search(body) else None,
                "size": _text(H6.search(body).group(1)) if H6.search(body) else None,
                "url": url.group(1) if url else None,
                "image": img.group(1) if img and "no-image" not in img.group(1) else None,
                "department": department,
            })
        return cards

    @staticmethod
    def parse_details(fragment: str) -> tuple[str | None, str | None, bool]:
        price = PRICE.search(fragment)
        old = OLD_PRICE.search(fragment)
        old_value = old.group(1) if old and old.group(1).replace(",", "").strip("0.") else None
        in_stock = "add_cart" in fragment and "out of stock" not in fragment.lower()
        return (price.group(1) if price else None), old_value, in_stock

    async def iter_listings(self) -> AsyncIterator[RawListing]:
        import json

        seen: set[str] = set()
        produced = 0
        for slug in self.cfg.departments:
            page_url = f"{self.cfg.base_url}/shop/{slug}"
            page = await self._open(f"/shop/{slug}")
            cat = re.search(r'id="cat_filter"[^>]*value="(\d+)"', page)
            if not cat:
                log.warning("department %s: category id not found (page layout changed?)", slug)
                continue
            offset, total = 0, None
            while total is None or offset < total:
                data = json.loads(await self._post("/shop_filter", {
                    "category": cat.group(1), "price": "1", "sorting": "most_popular", "offset": str(offset),
                }, page_url))
                total = int(data.get("total_products_count") or 0)
                current = int(data.get("current_products_count") or 0)
                cards = self.parse_cards(data.get("shop_filter", ""), slug)
                if not cards or current <= offset:
                    break
                offset = current
                for card in cards:
                    if card["id"] in seen:
                        continue
                    seen.add(card["id"])
                    if card["variant"] and not self.cfg.include_variants:
                        self.skipped_variants += 1
                        continue
                    details = await self._post(f"/get_product_details/{card['id']}/{self.cfg.price_zone}",
                                               {}, page_url)
                    price, old, in_stock = self.parse_details(details)
                    if price is None or not card["name"]:
                        self.skipped_no_price += 1
                        continue
                    yield RawListing(
                        external_id=card["id"], name=card["name"], brand=card["brand"], size_text=card["size"],
                        price=f"LBP {price}", currency=Currency.LBP,
                        regular_price=f"LBP {old}" if old else None,
                        in_stock=in_stock, url=card["url"], image_url=card["image"], category=slug,
                    )
                    produced += 1
                    if self.cfg.max_products and produced >= self.cfg.max_products:
                        return
        log.info("le_charcutier: %d products, %d multi-variant skipped, %d without price",
                 produced, self.skipped_variants, self.skipped_no_price)
