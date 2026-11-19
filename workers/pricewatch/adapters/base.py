"""The only contract a store integration has to implement.

To add a store:
  * If it uses a platform we already support (Shopify, WooCommerce, a JSON API,
    plain HTML) -> add an entry to config/stores.yaml. No code.
  * Otherwise -> create ONE new file (in pricewatch/adapters/, in a plugins folder,
    or in your own package) with a class decorated by @register_adapter("key").
The core (normalization, matching, storage, scheduling, alerts) is never edited.
"""
from __future__ import annotations

import logging
from abc import ABC
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, ClassVar

from pydantic import BaseModel, ConfigDict

from ..core.models import FlyerSource, RawBranch, RawListing
from ..pipeline.http import PoliteHttpClient


class AdapterConfig(BaseModel):
    """Base for per-adapter config models. Unknown keys are rejected to catch typos early."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Capabilities:
    catalog: bool = True          # can list products & prices
    branches: bool = False        # can list branches with coordinates
    branch_pricing: bool = False  # prices differ per branch
    flyers: bool = False          # can discover flyer/catalogue files


@dataclass
class AdapterContext:
    store_slug: str
    config: dict[str, Any]
    http: PoliteHttpClient
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("pricewatch.adapter"))
    # Lazily creates a Playwright page for JS-rendered sites (None if Playwright is not installed).
    browser_factory: Callable[[], Any] | None = None


class StoreAdapter(ABC):
    key: ClassVar[str] = ""
    capabilities: ClassVar[Capabilities] = Capabilities()
    Config: ClassVar[type[AdapterConfig]] = AdapterConfig

    def __init__(self, ctx: AdapterContext):
        self.ctx = ctx
        self.cfg = self.Config.model_validate(ctx.config)

    @property
    def http(self) -> PoliteHttpClient:
        return self.ctx.http

    async def fetch_branches(self) -> list[RawBranch]:
        return []

    async def iter_listings(self) -> AsyncIterator[RawListing]:
        if False:  # pragma: no cover  (makes this an async generator)
            yield
        raise NotImplementedError(f"{type(self).__name__} does not support catalog scraping")

    async def fetch_flyers(self) -> list[FlyerSource]:
        return []

    async def aclose(self) -> None:
        return None
