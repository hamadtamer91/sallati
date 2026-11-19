"""Postgres/Supabase repository (psycopg 3). Uses DATABASE_URL (Supabase: Project Settings -> Database)."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..core.currency import DEFAULT_USD_LBP
from ..core.matching import CanonicalCandidate, MatchResult
from ..core.categories import guess_category
from ..core.models import BaseUnit, FlyerSource, NormalizedListing, ProductIdentity, RawBranch, StoreRecord
from ..core.normalize import make_core_name
from ..vision.validator import ValidatedPromotion
from .base import OfferWrite


def _cand(r: dict) -> CanonicalCandidate:
    return CanonicalCandidate(str(r["id"]), make_core_name(r["name_norm"], r["brand_norm"]), r["brand_norm"],
                              BaseUnit(r["base_unit"]), Decimal(r["unit_size"]), r["pack_count"], r["gtin"])


class PostgresIndex:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self._cache: dict[tuple, list[CanonicalCandidate]] = {}

    def by_gtin(self, gtin: str) -> CanonicalCandidate | None:
        r = self.conn.execute(
            "select id, name_norm, brand_norm, base_unit, unit_size, pack_count, gtin "
            "from canonical_products where gtin = %s", (gtin,)).fetchone()
        return _cand(r) if r else None

    def candidates(self, brand_norm: str, base_unit: BaseUnit, pack_count: int,
                   unit_size: Decimal, tolerance: float) -> list[CanonicalCandidate]:
        key = (brand_norm, base_unit.value, pack_count, unit_size)
        if key not in self._cache:
            rows = self.conn.execute(
                "select id, name_norm, brand_norm, base_unit, unit_size, pack_count, gtin "
                "from canonical_products where brand_norm = %s and base_unit = %s and pack_count = %s "
                "and unit_size between %s and %s and is_active",
                (brand_norm, base_unit.value, pack_count,
                 unit_size * Decimal(1 - tolerance), unit_size * Decimal(1 + tolerance))).fetchall()
            self._cache[key] = [_cand(r) for r in rows]
        return self._cache[key]

    def invalidate(self) -> None:
        self._cache.clear()


class PostgresRepository:
    def __init__(self, dsn: str):
        self.conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        self._index: PostgresIndex | None = None
        self._categories: dict[str, int] | None = None

    def close(self) -> None:
        self.conn.close()

    # ---- stores & settings ------------------------------------------------
    def _store(self, r: dict) -> StoreRecord:
        return StoreRecord(str(r["id"]), r["slug"], r["name_en"], r["name_ar"], r["adapter_key"],
                           r["adapter_config"] or {}, float(r["rate_limit_rps"]), r["pricing_scope"], r["is_active"])

    def list_stores(self, active_only: bool = True) -> list[StoreRecord]:
        q = "select * from stores" + (" where is_active" if active_only else "") + " order by slug"
        return [self._store(r) for r in self.conn.execute(q).fetchall()]

    def get_store(self, slug: str) -> StoreRecord | None:
        r = self.conn.execute("select * from stores where slug = %s", (slug,)).fetchone()
        return self._store(r) if r else None

    def upsert_store(self, d: dict[str, Any]) -> str:
        r = self.conn.execute(
            """insert into stores (slug, name_en, name_ar, website, logo_url, adapter_key, adapter_config,
                                   pricing_scope, rate_limit_rps, schedule_cron, is_active)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (slug) do update set name_en=excluded.name_en, name_ar=excluded.name_ar,
                 website=excluded.website, logo_url=excluded.logo_url, adapter_key=excluded.adapter_key,
                 adapter_config=excluded.adapter_config, pricing_scope=excluded.pricing_scope,
                 rate_limit_rps=excluded.rate_limit_rps, schedule_cron=excluded.schedule_cron,
                 is_active=excluded.is_active
               returning id""",
            (d["slug"], d["name_en"], d["name_ar"], d.get("website"), d.get("logo_url"), d["adapter"],
             json.dumps(d.get("config", {})), d.get("pricing_scope", "chain"), d.get("rate_limit_rps", 0.5),
             d.get("schedule_cron", "0 */6 * * *"), d.get("active", True))).fetchone()
        return str(r["id"])

    def latest_usd_lbp(self) -> Decimal:
        r = self.conn.execute("select rate from exchange_rates order by effective_at desc limit 1").fetchone()
        return Decimal(r["rate"]) if r else DEFAULT_USD_LBP

    # ---- runs --------------------------------------------------------------
    def start_run(self, store_id: str, adapter_key: str, kind: str) -> int:
        return self.conn.execute(
            "insert into scraper_runs (store_id, adapter_key, run_kind) values (%s,%s,%s) returning id",
            (store_id, adapter_key, kind)).fetchone()["id"]

    def finish_run(self, run_id, status, stats, alerts, errors, baseline) -> None:
        self.conn.execute(
            """update scraper_runs set status=%s, finished_at=now(), items_seen=%s, items_upserted=%s,
                 items_failed=%s, new_products=%s, matched_products=%s, baseline_items=%s,
                 alerts=%s, error_sample=%s, stats=%s where id=%s""",
            (status, stats.get("items_seen", 0), stats.get("items_upserted", 0), stats.get("items_failed", 0),
             stats.get("new_products", 0), stats.get("matched_products", 0), baseline,
             json.dumps(alerts), json.dumps(errors[:20]), json.dumps(stats, default=str), run_id))

    def recent_item_counts(self, store_id: str, kind: str, limit: int = 5) -> list[int]:
        rows = self.conn.execute(
            "select items_seen from scraper_runs where store_id=%s and run_kind=%s and status='success' "
            "order by started_at desc limit %s", (store_id, kind, limit)).fetchall()
        return [r["items_seen"] for r in rows]

    # ---- catalogue -----------------------------------------------------------
    def upsert_branches(self, store_id: str, branches: list[RawBranch]) -> dict[str, str]:
        for b in branches:
            self.conn.execute(
                """insert into store_branches (store_id, external_id, name_en, name_ar, address, lat, lng,
                                               region_id, opening_hours)
                   values (%s,%s,%s,%s,%s,%s,%s,(select id from regions where slug=%s),%s)
                   on conflict (store_id, external_id) do update set name_en=excluded.name_en,
                     name_ar=excluded.name_ar, address=excluded.address, lat=excluded.lat, lng=excluded.lng,
                     region_id=coalesce(excluded.region_id, store_branches.region_id),
                     opening_hours=excluded.opening_hours, is_active=true""",
                (store_id, b.external_id, b.name_en, b.name_ar, b.address, b.lat, b.lng, b.region_slug,
                 json.dumps(b.opening_hours) if b.opening_hours else None))
        return self.branch_map(store_id)

    def branch_map(self, store_id: str) -> dict[str, str]:
        rows = self.conn.execute("select external_id, id from store_branches where store_id=%s", (store_id,))
        return {r["external_id"]: str(r["id"]) for r in rows.fetchall()}

    def catalog_index(self) -> PostgresIndex:
        if self._index is None:
            self._index = PostgresIndex(self.conn)
        return self._index

    def _category_id(self, slug: str | None) -> int | None:
        if self._categories is None:
            self._categories = {r["slug"]: r["id"] for r in self.conn.execute("select id, slug from categories")}
        return self._categories.get(slug) if slug else None

    def create_canonical(self, ident: ProductIdentity, display_name: str, brand: str | None,
                         category_hint: str | None, image_url: str | None) -> str:
        assert ident.quantity is not None
        is_ar = any("؀" <= ch <= "ۿ" for ch in display_name)
        cat = self._category_id(guess_category(category_hint, display_name))
        r = self.conn.execute(
            """insert into canonical_products (gtin, brand, brand_norm, name_en, name_ar, name_norm, category_id,
                                               base_unit, unit_size, pack_count, sold_by_weight, image_url)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (gtin) do update set gtin = excluded.gtin
               returning id""",
            (ident.gtin, brand, ident.brand_norm, None if is_ar else display_name, display_name if is_ar else None,
             ident.name_norm, cat, ident.quantity.base_unit.value, ident.quantity.unit_size,
             ident.quantity.pack_count, ident.quantity.sold_by_weight, image_url)).fetchone()
        self.catalog_index().invalidate()
        return str(r["id"])

    def upsert_source_product(self, store_id: str, n: NormalizedListing, canonical_id: str | None,
                              m: MatchResult) -> str:
        r = n.raw
        row = self.conn.execute(
            """insert into source_products (store_id, external_id, canonical_product_id, raw_name, raw_brand,
                 raw_size_text, raw_category, gtin, url, image_url, match_method, match_confidence,
                 needs_review, review_reason)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (store_id, external_id) do update set
                 canonical_product_id = case when source_products.match_method = 'manual'
                                             then source_products.canonical_product_id
                                             else excluded.canonical_product_id end,
                 match_method = case when source_products.match_method = 'manual' then 'manual'
                                     else excluded.match_method end,
                 raw_name=excluded.raw_name, raw_brand=excluded.raw_brand, raw_size_text=excluded.raw_size_text,
                 raw_category=excluded.raw_category, gtin=excluded.gtin, url=excluded.url,
                 image_url=excluded.image_url, match_confidence=excluded.match_confidence,
                 needs_review=excluded.needs_review and source_products.match_method <> 'manual',
                 review_reason=excluded.review_reason, last_seen_at=now()
               returning id, canonical_product_id""",
            (store_id, r.external_id, canonical_id, r.name, r.brand, r.size_text, r.category, n.gtin, r.url,
             r.image_url, m.method, m.confidence, m.needs_review, m.review_reason)).fetchone()
        if row["canonical_product_id"] and not m.needs_review:
            # every confidently matched listing teaches the canonical its name in the other language
            col = "name_ar" if any("\u0600" <= ch <= "\u06ff" for ch in r.name) else "name_en"
            self.conn.execute(f"update canonical_products set {col} = %s where id = %s and {col} is null",
                              (r.name, row["canonical_product_id"]))
            if r.image_url:
                self.conn.execute("update canonical_products set image_url = %s where id = %s and image_url is null",
                                  (r.image_url, row["canonical_product_id"]))
        return str(row["id"])

    def upsert_offer(self, source_product_id, canonical_id, store_id, branch_id, n: NormalizedListing,
                     run_id, source_kind) -> OfferWrite:
        row = self.conn.execute(
            """with prev as (
                 select price from product_offers
                 where source_product_id=%s and branch_id is not distinct from %s)
               insert into product_offers (source_product_id, canonical_product_id, store_id, branch_id, price,
                 currency, price_usd, regular_price_usd, unit_price_usd, unit_price_basis, in_stock, stale,
                 source_kind, scraper_run_id, observed_at)
               values (%s,(select canonical_product_id from source_products where id=%s),%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       false,%s,%s,now())
               on conflict on constraint product_offers_listing_branch_uniq do update set
                 canonical_product_id=excluded.canonical_product_id, price=excluded.price,
                 currency=excluded.currency, price_usd=excluded.price_usd,
                 regular_price_usd=excluded.regular_price_usd, unit_price_usd=excluded.unit_price_usd,
                 unit_price_basis=excluded.unit_price_basis, in_stock=excluded.in_stock, stale=false,
                 source_kind=excluded.source_kind, scraper_run_id=excluded.scraper_run_id, observed_at=now()
               returning (select price from prev) as prev_price""",
            (source_product_id, branch_id, source_product_id, source_product_id, store_id, branch_id, n.price,
             n.currency.value, n.price_usd, n.regular_price_usd, n.unit_price_usd, n.unit_price_basis,
             n.raw.in_stock, source_kind, run_id)).fetchone()
        prev = row["prev_price"]
        return OfferWrite(changed=prev is not None and Decimal(prev) != n.price, created=prev is None)

    def mark_unseen_stale(self, store_id: str, run_id: int) -> int:
        cur = self.conn.execute(
            "update product_offers set stale=true where store_id=%s and not stale "
            "and source_kind <> 'flyer' and (scraper_run_id is distinct from %s)", (store_id, run_id))
        return cur.rowcount

    def reference_price_usd(self, canonical_id: str) -> Decimal | None:
        r = self.conn.execute(
            "select percentile_cont(0.5) within group (order by price_usd) as p from product_offers "
            "where canonical_product_id=%s and not stale and in_stock", (canonical_id,)).fetchone()
        return Decimal(str(r["p"])) if r and r["p"] is not None else None

    # ---- flyers & promotions -----------------------------------------------------
    def flyer_exists(self, sha256: str) -> bool:
        return self.conn.execute("select 1 from flyers where file_sha256=%s", (sha256,)).fetchone() is not None

    def create_flyer(self, store_id, source: FlyerSource, sha256, mime, run_id) -> str:
        return str(self.conn.execute(
            """insert into flyers (store_id, source_url, file_sha256, mime_type, title, valid_from, valid_until,
                                   status, scraper_run_id) values (%s,%s,%s,%s,%s,%s,%s,'processing',%s) returning id""",
            (store_id, source.url, sha256, mime, source.title, source.valid_from, source.valid_until, run_id)
        ).fetchone()["id"])

    def finish_flyer(self, flyer_id, status, page_count, extracted, accepted, llm_calls, valid_from, valid_until, error) -> None:
        self.conn.execute(
            """update flyers set status=%s, page_count=%s, items_extracted=%s, items_accepted=%s, llm_calls=%s,
                 valid_from=coalesce(%s, valid_from), valid_until=coalesce(%s, valid_until), error=%s,
                 processed_at=now() where id=%s""",
            (status, page_count, extracted, accepted, llm_calls, valid_from, valid_until, error, flyer_id))

    def upsert_promotion(self, store_id, vp: ValidatedPromotion, ident: ProductIdentity, canonical_id,
                         flyer_id, page, dedupe_key) -> bool:
        raw = vp.raw.model_dump(mode="json")
        row = self.conn.execute(
            """insert into promotions (store_id, canonical_product_id, flyer_id, flyer_page, title, brand, size_text,
                 promo_type, promo_price, currency, promo_price_usd, regular_price_usd, percent_off, buy_qty, get_qty,
                 valid_from, valid_until, dates_inferred, status, confidence, review_reasons, dedupe_key, raw)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (dedupe_key) do update set
                 canonical_product_id=coalesce(promotions.canonical_product_id, excluded.canonical_product_id),
                 confidence=greatest(promotions.confidence, excluded.confidence),
                 status=case when promotions.status in ('rejected','expired') then promotions.status
                             else excluded.status end
               returning (xmax = 0) as inserted""",
            (store_id, canonical_id, flyer_id, page, vp.raw.name, vp.raw.brand, vp.raw.size_text, vp.promo_type,
             vp.price, vp.currency.value if vp.currency else None, vp.promo_price_usd, vp.regular_price_usd,
             vp.percent_off, vp.buy_qty, vp.get_qty, vp.valid_from, vp.valid_until, vp.dates_inferred,
             vp.status, round(vp.confidence, 3), vp.reasons, dedupe_key, json.dumps(raw))).fetchone()
        return bool(row["inserted"])

    def expire_promotions(self) -> int:
        return self.conn.execute("select expire_promotions() as n").fetchone()["n"]
