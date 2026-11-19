"""Config-driven adapter for any JSON API (store website or delivery-app web API).

Everything is described in YAML: endpoint, pagination, where the items are and which
field is which. Example in config/stores.yaml.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..core.models import Currency, RawBranch, RawListing
from ._util import clean, pluck
from .base import AdapterConfig, Capabilities, StoreAdapter
from .registry import register_adapter


class FieldMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str
    name: str
    price: str
    regular_price: str | None = None
    currency: str | None = None
    brand: str | None = None
    size_text: str | None = None
    gtin: str | None = None
    in_stock: str | None = None
    url: str | None = None
    image_url: str | None = None
    category: str | None = None


class Pagination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["page", "offset", "cursor", "none"] = "page"
    param: str = "page"
    size_param: str | None = "limit"
    size: int = Field(100, ge=1, le=1000)
    start: int = 1
    cursor_path: str | None = None      # where the next cursor is in the response
    max_pages: int = Field(500, ge=1)


class JsonApiConfig(AdapterConfig):
    url: str                              # may contain {branch}
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = {}
    params: dict[str, Any] = {}
    body: dict[str, Any] | None = None
    items_path: str = ""                  # "" = the response itself is the list
    fields: FieldMap
    pagination: Pagination = Pagination()
    currency: Currency | None = None
    price_divisor: float = 1.0            # e.g. 100 when the API returns cents
    url_prefix: str = ""
    branches: list[RawBranch] = []        # static branch list (if the API has none)
    branch_placeholder_values: list[str] = []  # iterate {branch} over these external ids


@register_adapter("json_api")
class JsonApiAdapter(StoreAdapter):
    capabilities = Capabilities(catalog=True, branches=True, branch_pricing=True)
    Config = JsonApiConfig
    cfg: JsonApiConfig

    async def fetch_branches(self) -> list[RawBranch]:
        return list(self.cfg.branches)

    async def _call(self, url: str, extra: dict[str, Any]) -> Any:
        params = {**self.cfg.params}
        body = dict(self.cfg.body or {}) if self.cfg.body is not None else None
        target = body if (self.cfg.method == "POST" and body is not None) else params
        target.update(extra)
        resp = await self.http.request(self.cfg.method, url, params=params, json=body, headers=self.cfg.headers)
        return resp.json()

    def _to_listing(self, item: dict, branch: str | None) -> RawListing:
        f = self.cfg.fields
        price = pluck(item, f.price)
        if price is not None and self.cfg.price_divisor != 1:
            price = float(price) / self.cfg.price_divisor
        regular = pluck(item, f.regular_price)
        if regular is not None and self.cfg.price_divisor != 1:
            regular = float(regular) / self.cfg.price_divisor
        cur = pluck(item, f.currency) if f.currency else None
        url = pluck(item, f.url)
        stock = pluck(item, f.in_stock, True) if f.in_stock else True
        return RawListing(
            external_id=str(pluck(item, f.external_id)),
            name=clean(pluck(item, f.name)) or "",
            price=price,
            currency=Currency(cur) if cur in ("USD", "LBP") else self.cfg.currency,
            regular_price=regular,
            brand=clean(pluck(item, f.brand)),
            size_text=clean(pluck(item, f.size_text)),
            gtin=clean(pluck(item, f.gtin)),
            in_stock=stock not in (False, 0, "0", "false", "out_of_stock", "OUT_OF_STOCK"),
            url=f"{self.cfg.url_prefix}{url}" if url and self.cfg.url_prefix else url,
            image_url=clean(pluck(item, f.image_url)),
            category=clean(pluck(item, f.category)),
            branch_external_id=branch,
        )

    async def iter_listings(self) -> AsyncIterator[RawListing]:
        branches: list[str | None] = list(self.cfg.branch_placeholder_values) or [None]
        p = self.cfg.pagination
        for branch in branches:
            url = self.cfg.url.format(branch=branch or "")
            cursor: Any = None
            for i in range(p.max_pages):
                extra: dict[str, Any] = {}
                if p.size_param and p.type != "none":
                    extra[p.size_param] = p.size
                if p.type == "page":
                    extra[p.param] = p.start + i
                elif p.type == "offset":
                    extra[p.param] = p.start + i * p.size
                elif p.type == "cursor" and cursor:
                    extra[p.param] = cursor
                data = await self._call(url, extra)
                items = pluck(data, self.cfg.items_path) if self.cfg.items_path else data
                if not items:
                    break
                for item in items:
                    yield self._to_listing(item, branch)
                if p.type == "none":
                    break
                if p.type == "cursor":
                    cursor = pluck(data, p.cursor_path)
                    if not cursor:
                        break
                elif len(items) < p.size:
                    break
