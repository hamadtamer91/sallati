"""In-memory repository: used by tests and `--dry-run`."""
from __future__ import annotations

import itertools
import statistics
import uuid
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from ..core.currency import DEFAULT_USD_LBP
from ..core.matching import CanonicalCandidate, MatchResult
from ..core.models import BaseUnit, FlyerSource, NormalizedListing, ProductIdentity, RawBranch, StoreRecord
from ..core.normalize import make_core_name
from ..vision.validator import ValidatedPromotion
from .base import OfferWrite


class MemoryIndex:
    def __init__(self, repo: "MemoryRepository"):
        self.repo = repo

    def by_gtin(self, gtin: str) -> CanonicalCandidate | None:
        for c in self.repo.canonicals.values():
            if c["gtin"] == gtin:
                return self._cand(c)
        return None

    def candidates(self, brand_norm: str, base_unit: BaseUnit, pack_count: int,
                   unit_size: Decimal, tolerance: float) -> list[CanonicalCandidate]:
        lo, hi = unit_size * Decimal(1 - tolerance), unit_size * Decimal(1 + tolerance)
        return [self._cand(c) for c in self.repo.canonicals.values()
                if c["brand_norm"] == brand_norm and c["base_unit"] == base_unit.value
                and c["pack_count"] == pack_count and lo <= c["unit_size"] <= hi]

    @staticmethod
    def _cand(c: dict) -> CanonicalCandidate:
        return CanonicalCandidate(c["id"], make_core_name(c["name_norm"], c["brand_norm"]), c["brand_norm"],
                                  BaseUnit(c["base_unit"]), c["unit_size"], c["pack_count"], c["gtin"])


class MemoryRepository:
    def __init__(self, usd_lbp: Decimal = DEFAULT_USD_LBP):
        self.usd_lbp = usd_lbp
        self.stores: dict[str, StoreRecord] = {}
        self.branches: dict[tuple[str, str], str] = {}
        self.runs: dict[int, dict] = {}
        self.canonicals: dict[str, dict] = {}
        self.source_products: dict[tuple[str, str], dict] = {}
        self.offers: dict[tuple[str, str | None], dict] = {}
        self.flyers: dict[str, dict] = {}
        self.promotions: dict[str, dict] = {}
        self._run_ids = itertools.count(1)

    # stores
    def list_stores(self, active_only: bool = True) -> list[StoreRecord]:
        return [s for s in self.stores.values() if s.is_active or not active_only]

    def get_store(self, slug: str) -> StoreRecord | None:
        return self.stores.get(slug)

    def upsert_store(self, data: dict[str, Any]) -> str:
        existing = self.stores.get(data["slug"])
        sid = existing.id if existing else str(uuid.uuid4())
        self.stores[data["slug"]] = StoreRecord(
            id=sid, slug=data["slug"], name_en=data["name_en"], name_ar=data["name_ar"],
            adapter_key=data["adapter"], adapter_config=data.get("config", {}),
            rate_limit_rps=float(data.get("rate_limit_rps", 0.5)),
            pricing_scope=data.get("pricing_scope", "chain"), is_active=data.get("active", True))
        return sid

    def latest_usd_lbp(self) -> Decimal:
        return self.usd_lbp

    # runs
    def start_run(self, store_id: str, adapter_key: str, kind: str) -> int:
        rid = next(self._run_ids)
        self.runs[rid] = {"id": rid, "store_id": store_id, "kind": kind, "status": "running"}
        return rid

    def finish_run(self, run_id, status, stats, alerts, errors, baseline) -> None:
        self.runs[run_id].update(status=status, stats=stats, alerts=alerts, errors=errors, baseline=baseline)

    def recent_item_counts(self, store_id: str, kind: str, limit: int = 5) -> list[int]:
        runs = [r for r in self.runs.values() if r["store_id"] == store_id and r["kind"] == kind
                and r["status"] == "success"]
        return [r["stats"]["items_seen"] for r in runs[-limit:]]

    def hours_since_last_run(self, store_id: str, kind: str) -> float | None:
        return None

    # catalogue
    def upsert_branches(self, store_id: str, branches: list[RawBranch]) -> dict[str, str]:
        for b in branches:
            self.branches.setdefault((store_id, b.external_id), str(uuid.uuid4()))
        return self.branch_map(store_id)

    def branch_map(self, store_id: str) -> dict[str, str]:
        return {ext: bid for (sid, ext), bid in self.branches.items() if sid == store_id}

    def catalog_index(self) -> MemoryIndex:
        return MemoryIndex(self)

    def create_canonical(self, ident: ProductIdentity, display_name: str, brand: str | None,
                         category_hint: str | None, image_url: str | None) -> str:
        assert ident.quantity is not None
        cid = str(uuid.uuid4())
        self.canonicals[cid] = {
            "id": cid, "gtin": ident.gtin, "brand": brand, "brand_norm": ident.brand_norm,
            "name": display_name, "name_norm": ident.name_norm, "base_unit": ident.quantity.base_unit.value,
            "unit_size": ident.quantity.unit_size, "pack_count": ident.quantity.pack_count,
            "category_hint": category_hint,
        }
        return cid

    def upsert_source_product(self, store_id, n: NormalizedListing, canonical_id, m: MatchResult) -> str:
        key = (store_id, n.raw.external_id)
        sp = self.source_products.get(key) or {"id": str(uuid.uuid4())}
        sp.update(store_id=store_id, external_id=n.raw.external_id, name=n.raw.name,
                  canonical_id=canonical_id, method=m.method, confidence=m.confidence, review=m.needs_review)
        self.source_products[key] = sp
        return sp["id"]

    def upsert_offer(self, source_product_id, canonical_id, store_id, branch_id, n, run_id, source_kind) -> OfferWrite:
        key = (source_product_id, branch_id)
        prev = self.offers.get(key)
        self.offers[key] = {"store_id": store_id, "canonical_id": canonical_id, "price_usd": n.price_usd,
                            "unit_price_usd": n.unit_price_usd, "run_id": run_id, "stale": False,
                            "in_stock": n.raw.in_stock}
        return OfferWrite(changed=bool(prev and prev["price_usd"] != n.price_usd), created=prev is None)

    def mark_unseen_stale(self, store_id: str, run_id: int) -> int:
        n = 0
        for o in self.offers.values():
            if o["store_id"] == store_id and o["run_id"] != run_id and not o["stale"]:
                o["stale"] = True
                n += 1
        return n

    def reference_price_usd(self, canonical_id: str) -> Decimal | None:
        prices = [o["price_usd"] for o in self.offers.values() if o["canonical_id"] == canonical_id and not o["stale"]]
        return Decimal(statistics.median(prices)) if prices else None

    # flyers
    def flyer_exists(self, sha256: str) -> bool:
        return any(f["sha"] == sha256 for f in self.flyers.values())

    def create_flyer(self, store_id, source: FlyerSource, sha256, mime, run_id) -> str:
        fid = str(uuid.uuid4())
        self.flyers[fid] = {"id": fid, "store_id": store_id, "sha": sha256, "url": source.url, "status": "processing"}
        return fid

    def finish_flyer(self, flyer_id, status, page_count, extracted, accepted, llm_calls, valid_from, valid_until, error) -> None:
        self.flyers[flyer_id].update(status=status, pages=page_count, extracted=extracted, accepted=accepted,
                                     llm_calls=llm_calls, valid_from=valid_from, valid_until=valid_until, error=error)

    def upsert_promotion(self, store_id, vp: ValidatedPromotion, ident, canonical_id, flyer_id, page, dedupe_key) -> bool:
        created = dedupe_key not in self.promotions
        row = {k: v for k, v in asdict(vp).items() if k != "raw"}
        row.update(store_id=store_id, canonical_id=canonical_id, flyer_id=flyer_id, page=page, title=vp.raw.name)
        self.promotions[dedupe_key] = row
        return created

    def expire_promotions(self) -> int:
        return 0
