"""Discovers flyer/catalogue files (PDF or images) linked from a store's offers page."""
from __future__ import annotations

import re

from pydantic import Field

from ..core.models import FlyerSource
from ._util import parse_html
from .base import AdapterConfig, Capabilities, StoreAdapter
from .registry import register_adapter
from urllib.parse import urljoin


class FlyerLinksConfig(AdapterConfig):
    page_url: str
    link_selector: str = "a[href], img[src], img[data-src]"
    include_pattern: str = r"\.(pdf|jpe?g|png|webp)(\?|$)"
    exclude_pattern: str | None = r"(logo|icon|banner-small|favicon)"
    max_flyers: int = Field(6, ge=1, le=50)


@register_adapter("flyer_links")
class FlyerLinksAdapter(StoreAdapter):
    capabilities = Capabilities(catalog=False, flyers=True)
    Config = FlyerLinksConfig
    cfg: FlyerLinksConfig

    async def fetch_flyers(self) -> list[FlyerSource]:
        html = await self.http.get_text(self.cfg.page_url)
        inc = re.compile(self.cfg.include_pattern, re.I)
        exc = re.compile(self.cfg.exclude_pattern, re.I) if self.cfg.exclude_pattern else None
        seen: list[str] = []
        for node in parse_html(html).css(self.cfg.link_selector):
            raw = node.attributes.get("href") or node.attributes.get("data-src") or node.attributes.get("src")
            if not raw:
                continue
            url = urljoin(self.cfg.page_url, raw)
            if inc.search(url) and not (exc and exc.search(url)) and url not in seen:
                seen.append(url)
        return [FlyerSource(url=u, title=u.rsplit("/", 1)[-1]) for u in seen[: self.cfg.max_flyers]]
