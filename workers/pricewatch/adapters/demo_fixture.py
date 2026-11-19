"""Offline adapter backed by a JSON fixture. Used for tests and local demos."""
from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

from ..core.models import FlyerSource, RawBranch, RawListing
from .base import AdapterConfig, Capabilities, StoreAdapter
from .registry import register_adapter

WORKERS_DIR = Path(__file__).resolve().parents[2]


class DemoConfig(AdapterConfig):
    fixture: str
    limit: int | None = None


@register_adapter("demo_fixture")
class DemoFixtureAdapter(StoreAdapter):
    capabilities = Capabilities(catalog=True, branches=True, branch_pricing=True, flyers=True)
    Config = DemoConfig
    cfg: DemoConfig

    def _data(self) -> dict:
        p = Path(self.cfg.fixture)
        p = p if p.is_absolute() else WORKERS_DIR / p
        return json.loads(p.read_text(encoding="utf-8"))

    async def fetch_branches(self) -> list[RawBranch]:
        return [RawBranch.model_validate(b) for b in self._data().get("branches", [])]

    async def iter_listings(self) -> AsyncIterator[RawListing]:
        for i, item in enumerate(self._data().get("listings", [])):
            if self.cfg.limit is not None and i >= self.cfg.limit:
                return
            yield RawListing.model_validate(item)

    async def fetch_flyers(self) -> list[FlyerSource]:
        out = []
        for f in self._data().get("flyers", []):
            path = Path(f["file"])
            path = path if path.is_absolute() else WORKERS_DIR / path
            out.append(FlyerSource(url=path.as_uri(), title=f.get("title"),
                                   valid_from=f.get("valid_from"), valid_until=f.get("valid_until")))
        return out
