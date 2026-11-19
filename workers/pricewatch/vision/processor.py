"""Flyer -> pages -> tiles -> Vision LLM -> validated, matched promotions."""
from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..core.matching import ProductMatcher
from ..core.models import Currency, FlyerSource, RawPromotion, StoreRecord
from ..core.normalize import Normalizer
from ..core.text import normalize_text
from ..storage.base import Repository
from .client import VisionLLM
from .schema import (HEADER_SCHEMA, TILE_SCHEMA, FlyerHeader, TileExtraction, header_instruction,
                     tile_instruction)
from .tiling import make_tiles, overview, render_document
from .validator import PromotionValidator, ValidatedPromotion, parse_date

log = logging.getLogger(__name__)


@dataclass
class FlyerResult:
    flyer_id: str | None
    skipped: bool = False
    pages: int = 0
    tiles: int = 0
    extracted: int = 0
    accepted: int = 0
    pending_review: int = 0
    rejected: int = 0
    expired: int = 0
    matched: int = 0
    llm_errors: int = 0
    status: str = "done"
    errors: list[str] = field(default_factory=list)


def _dedupe(items: list[RawPromotion]) -> list[RawPromotion]:
    """Overlapping tiles (and repeated pages) show the same offer twice: keep the most confident reading."""
    best: dict[tuple, RawPromotion] = {}
    for it in items:
        key = (normalize_text(it.name), normalize_text(it.size_text), str(it.price), it.promo_type)
        if key not in best or it.confidence > best[key].confidence:
            best[key] = it
    return list(best.values())


class FlyerProcessor:
    def __init__(self, llm: VisionLLM, repo: Repository, normalizer: Normalizer, matcher: ProductMatcher,
                 today: date | None = None, tile_px: int = 1100, overlap: float = 0.15, dpi: int = 170,
                 max_workers: int = 3, max_pages: int = 40):
        self.llm, self.repo, self.normalizer, self.matcher = llm, repo, normalizer, matcher
        self.today = today or date.today()
        self.tile_px, self.overlap, self.dpi = tile_px, overlap, dpi
        self.max_workers, self.max_pages = max_workers, max_pages

    def _header(self, page_jpeg: bytes) -> FlyerHeader:
        try:
            data = self.llm.extract(page_jpeg, "image/jpeg", header_instruction(self.today.isoformat()), HEADER_SCHEMA)
            return FlyerHeader.model_validate(data)
        except Exception as e:  # header is best effort
            log.warning("flyer header extraction failed: %s", e)
            return FlyerHeader()

    def process(self, store: StoreRecord, content: bytes, mime: str | None, source: FlyerSource,
                run_id: int | None = None) -> FlyerResult:
        sha = hashlib.sha256(content).hexdigest()
        if self.repo.flyer_exists(sha):
            return FlyerResult(None, skipped=True, status="skipped")
        flyer_id = self.repo.create_flyer(store.id, source, sha, mime, run_id)
        res = FlyerResult(flyer_id)
        calls_before = getattr(self.llm, "calls", 0)
        try:
            pages = render_document(content, mime, self.dpi, self.max_pages)
            res.pages = len(pages)
            header = self._header(overview(pages[0]))
            flyer_from = parse_date(header.flyer_valid_from, self.today) or source.valid_from
            flyer_until = parse_date(header.flyer_valid_until, self.today) or source.valid_until
            default_cur = Currency(header.default_currency) if header.default_currency else None

            jobs = [(p_no, t) for p_no, img in enumerate(pages, 1)
                    for t in make_tiles(img, p_no, self.tile_px, self.overlap)]
            res.tiles = len(jobs)

            def run(job):
                p_no, tile = job
                instr = tile_instruction(self.today.isoformat(), p_no, tile.label, store.name_en)
                return p_no, TileExtraction.model_validate(self.llm.extract(tile.jpeg(), "image/jpeg", instr, TILE_SCHEMA))

            raw_items: list[RawPromotion] = []
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = [pool.submit(run, j) for j in jobs]
                for fut in futures:
                    try:
                        p_no, ext = fut.result()
                    except Exception as e:  # one bad tile must not lose the whole flyer
                        res.llm_errors += 1
                        res.errors.append(f"tile: {e}"[:300])
                        continue
                    flyer_from = flyer_from or parse_date(ext.flyer_valid_from, self.today)
                    flyer_until = flyer_until or parse_date(ext.flyer_valid_until, self.today)
                    raw_items += [RawPromotion(**it.model_dump(), page=p_no) for it in ext.items]

            items = _dedupe(raw_items)
            res.extracted = len(items)
            validator = PromotionValidator(self.today, self.normalizer.usd_lbp)
            for it in items:
                self._store_item(store, it, validator, flyer_from, flyer_until, default_cur, flyer_id, res)

            if res.tiles and res.llm_errors / res.tiles > 0.3:
                res.status = "needs_review"
            elif res.pending_review > res.accepted:
                res.status = "needs_review"
            self.repo.finish_flyer(flyer_id, res.status, res.pages, res.extracted, res.accepted,
                                   getattr(self.llm, "calls", 0) - calls_before, flyer_from, flyer_until,
                                   "; ".join(res.errors[:5]) or None)
        except Exception as e:
            log.exception("flyer processing failed")
            res.status = "failed"
            res.errors.append(str(e)[:500])
            self.repo.finish_flyer(flyer_id, "failed", res.pages, res.extracted, res.accepted,
                                   getattr(self.llm, "calls", 0) - calls_before, None, None, str(e)[:500])
        return res

    def _store_item(self, store: StoreRecord, it: RawPromotion, validator: PromotionValidator,
                    vf: date | None, vu: date | None, cur: Currency | None, flyer_id: str, res: FlyerResult) -> None:
        vp: ValidatedPromotion = validator.validate(it, vf, vu, cur)
        if vp.status in ("rejected", "expired"):
            setattr(res, vp.status, getattr(res, vp.status) + 1)
            return
        ident = self.normalizer.identity(it.name, it.brand, it.size_text, it.gtin)
        m = self.matcher.match(ident, allow_create=False)
        canonical_id = m.canonical_id if (m.canonical_id and not m.needs_review) else None
        if canonical_id:
            res.matched += 1
            ref = self.repo.reference_price_usd(canonical_id)
            unit = vp.promo_price_usd / vp.buy_qty if (vp.promo_type == "multibuy" and vp.promo_price_usd and vp.buy_qty) \
                else vp.promo_price_usd
            if ref and unit:
                if unit > ref * Decimal("1.15"):
                    vp.reasons.append("not_cheaper_than_market")
                elif unit < ref * Decimal("0.2"):
                    vp.reasons.append("suspiciously_cheap")
                if {"not_cheaper_than_market", "suspiciously_cheap"} & set(vp.reasons):
                    vp.status = "pending_review"
        key = hashlib.sha1("|".join(map(str, [
            store.id, ident.name_norm, normalize_text(it.size_text), vp.promo_type, vp.price, vp.percent_off,
            vp.buy_qty, vp.valid_from, vp.valid_until])).encode()).hexdigest()
        self.repo.upsert_promotion(store.id, vp, ident, canonical_id, flyer_id, it.page, key)
        if vp.status == "active":
            res.accepted += 1
        else:
            res.pending_review += 1
