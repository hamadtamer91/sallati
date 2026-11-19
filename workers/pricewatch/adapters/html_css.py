"""Config-driven HTML adapter: CSS selectors in YAML, optional JS rendering via Playwright."""
from __future__ import annotations

import hashlib
from typing import Any, AsyncIterator

from pydantic import Field

from ..core.models import Currency, RawListing
from ._util import css_value, parse_html
from .base import AdapterConfig, Capabilities, StoreAdapter
from .registry import register_adapter


class HtmlCssConfig(AdapterConfig):
    start_urls: list[str] = Field(min_length=1)
    item_selector: str
    # field -> selector ("h3.name", "a.link@href", "img@data-src"). Required: name, price.
    fields: dict[str, str]
    next_page_selector: str | None = None   # e.g. "a.next@href"
    max_pages: int = Field(50, ge=1)
    render_js: bool = False
    wait_for: str | None = None
    currency: Currency | None = None
    out_of_stock_text: list[str] = ["out of stock", "غير متوفر", "نفذ"]


@register_adapter("html_css")
class HtmlCssAdapter(StoreAdapter):
    capabilities = Capabilities(catalog=True)
    Config = HtmlCssConfig
    cfg: HtmlCssConfig
    _pw: Any = None
    _browser: Any = None

    async def _fetch(self, url: str) -> str:
        if not self.cfg.render_js:
            return await self.http.get_text(url)
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("render_js needs: pip install 'pricewatch[browser]' && playwright install chromium") from e
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch()
        page = await self._browser.new_page(user_agent=self.http.user_agent)
        try:
            await page.goto(url, wait_until="networkidle")
            if self.cfg.wait_for:
                await page.wait_for_selector(self.cfg.wait_for, timeout=15000)
            return await page.content()
        finally:
            await page.close()

    def parse_page(self, html: str, page_url: str) -> tuple[list[RawListing], str | None]:
        tree = parse_html(html)
        out: list[RawListing] = []
        for node in tree.css(self.cfg.item_selector):
            vals = {k: css_value(node, sel, page_url) for k, sel in self.cfg.fields.items()}
            if not vals.get("name") or not vals.get("price"):
                continue
            ext = vals.pop("external_id", None) or vals.get("url") or hashlib.sha1(
                vals["name"].encode()).hexdigest()[:16]
            stock_text = (vals.pop("stock", None) or "").lower()
            out.append(RawListing(
                external_id=ext,
                name=vals["name"],
                price=vals["price"],
                currency=self.cfg.currency,
                regular_price=vals.get("regular_price"),
                brand=vals.get("brand"),
                size_text=vals.get("size_text"),
                gtin=vals.get("gtin"),
                in_stock=not any(t in stock_text for t in self.cfg.out_of_stock_text),
                url=vals.get("url"),
                image_url=vals.get("image_url"),
                category=vals.get("category"),
            ))
        nxt = None
        if self.cfg.next_page_selector:
            root = tree.body or tree.root
            nxt = css_value(root, self.cfg.next_page_selector, page_url) if root else None
        return out, nxt

    async def iter_listings(self) -> AsyncIterator[RawListing]:
        for start in self.cfg.start_urls:
            url: str | None = start
            seen: set[str] = set()
            for _ in range(self.cfg.max_pages):
                if not url or url in seen:
                    break
                seen.add(url)
                items, url = self.parse_page(await self._fetch(url), url)
                for it in items:
                    yield it

    async def aclose(self) -> None:
        if self._browser:
            await self._browser.close()
            await self._pw.stop()
