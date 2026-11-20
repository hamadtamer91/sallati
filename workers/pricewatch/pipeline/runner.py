"""Orchestration: one store at a time, adapter -> normalize -> match -> persist -> health -> alerts.

Nothing here knows about any specific store."""
from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit
from urllib.request import url2pathname

from ..adapters.base import AdapterContext, StoreAdapter
from ..adapters.registry import build_adapter
from ..core.categories import guess_category
from ..core.matching import ProductMatcher
from ..core.models import StoreRecord
from ..core.normalize import NormalizationError, Normalizer
from ..storage.base import Repository
from ..vision.client import VisionLLM
from ..vision.processor import FlyerProcessor, FlyerResult
from .health import RunStats, evaluate, is_critical
from .http import PoliteHttpClient
from .notify import LogNotifier, Notifier

log = logging.getLogger(__name__)
HttpFactory = Callable[[StoreRecord], PoliteHttpClient]


def default_http_factory(store: StoreRecord) -> PoliteHttpClient:
    cfg = store.adapter_config
    return PoliteHttpClient(rate_per_sec=store.rate_limit_rps,
                            respect_robots=cfg.get("respect_robots", True),
                            headers=cfg.get("http_headers"))


def split_config(cfg: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Core-level keys are removed before the adapter validates its own config."""
    main = {k: v for k, v in cfg.items() if k not in {"flyers", "respect_robots", "http_headers", "min_hours_between_runs", "branch_overrides"}}
    return main, cfg.get("flyers")


@dataclass
class RunSummary:
    store: str
    kind: str
    status: str
    run_id: int | None = None
    stats: dict = field(default_factory=dict)
    alerts: list[dict] = field(default_factory=list)


class Runner:
    def __init__(self, repo: Repository, notifier: Notifier | None = None,
                 http_factory: HttpFactory = default_http_factory, vision: VisionLLM | None = None,
                 today: date | None = None):
        self.repo = repo
        self.notifier = notifier or LogNotifier()
        self.http_factory = http_factory
        self.vision = vision
        self.today = today

    def _adapter(self, store: StoreRecord, http: PoliteHttpClient, flyers: bool = False) -> StoreAdapter:
        main, flyer_cfg = split_config(store.adapter_config)
        if flyers and flyer_cfg is not None:
            key = flyer_cfg.get("adapter", "flyer_links")
            cfg = {k: v for k, v in flyer_cfg.items() if k != "adapter"}
            return build_adapter(key, AdapterContext(store.slug, cfg, http))
        ctx = AdapterContext(store.slug, main, http)
        adapter = build_adapter(store.adapter_key, ctx)
        ctx.config = {**ctx.config, "branch_overrides": store.adapter_config.get("branch_overrides") or {}}
        return adapter

    # ------------------------------------------------------------------ catalog
    async def run_catalog(self, store: StoreRecord, limit: int | None = None) -> RunSummary:
        run_id = self.repo.start_run(store.id, store.adapter_key, "catalog")
        stats, errors = RunStats(), []
        status, alerts, baseline = "failed", [], None
        http = self.http_factory(store)
        adapter: StoreAdapter | None = None
        try:
            adapter = self._adapter(store, http)
            normalizer = Normalizer(self.repo.latest_usd_lbp())
            matcher = ProductMatcher(self.repo.catalog_index())
            branches = self.repo.upsert_branches(store.id, await adapter.fetch_branches()) \
                if adapter.capabilities.branches else self.repo.branch_map(store.id)
            source_kind = "html" if store.adapter_key.startswith("html") else "api"

            async for raw in adapter.iter_listings():
                if limit is not None and stats.items_seen >= limit:
                    break
                stats.items_seen += 1
                try:
                    n = normalizer.normalize_listing(raw)
                except (NormalizationError, ValueError) as e:
                    stats.items_failed += 1
                    stats.failure_reasons[str(e).split(":")[0]] += 1
                    if len(errors) < 20:
                        errors.append({"external_id": raw.external_id, "name": raw.name, "error": str(e)})
                    continue
                if n.quantity is None:
                    stats.quantity_unparsed += 1
                m = matcher.match(n)
                canonical_id = m.canonical_id
                if canonical_id is None and m.create_new and n.quantity is not None:
                    canonical_id = self.repo.create_canonical(
                        n, raw.name, raw.brand, raw.category or guess_category(raw.name), raw.image_url)
                    stats.new_products += 1
                elif canonical_id:
                    stats.matched_products += 1
                else:
                    stats.unmatched += 1
                stats.needs_review += int(m.needs_review)
                sp_id = self.repo.upsert_source_product(store.id, n, canonical_id, m)
                branch_id = branches.get(raw.branch_external_id) if raw.branch_external_id else None
                if raw.branch_external_id and branch_id is None:
                    stats.failure_reasons["unknown_branch"] += 1
                w = self.repo.upsert_offer(sp_id, canonical_id, store.id, branch_id, n, run_id, source_kind)
                stats.items_upserted += 1
                stats.price_changes += int(w.changed)

            alert_objs, baseline = evaluate(stats, self.repo.recent_item_counts(store.id, "catalog"))
            alerts = [a.as_dict() for a in alert_objs]
            if is_critical(alert_objs):
                status = "partial"   # keep yesterday's prices: do NOT mark unseen offers as stale
            else:
                stats.stale_marked = self.repo.mark_unseen_stale(store.id, run_id)
                status = "success"
        except Exception as e:
            log.exception("catalog run failed for %s", store.slug)
            alerts.append({"code": "run_crashed", "severity": "critical", "message": f"{type(e).__name__}: {e}"[:400]})
            errors.append({"error": repr(e)[:400]})
        finally:
            if adapter:
                await adapter.aclose()
            await http.aclose()
            self.repo.finish_run(run_id, status, stats.as_dict(), alerts, errors, baseline)
        if alerts:
            self.notifier.send(f"[{status.upper()}] {store.name_en} catalog run #{run_id}",
                               [f"{a['severity']}: {a['message']}" for a in alerts])
        return RunSummary(store.slug, "catalog", status, run_id, stats.as_dict(), alerts)

    # ------------------------------------------------------------------ flyers
    async def run_flyers(self, store: StoreRecord) -> RunSummary:
        if self.vision is None:
            raise RuntimeError("flyer runs need a vision client (set ANTHROPIC_API_KEY)")
        run_id = self.repo.start_run(store.id, store.adapter_key, "flyer")
        http = self.http_factory(store)
        results: list[FlyerResult] = []
        alerts: list[dict] = []
        status = "failed"
        adapter: StoreAdapter | None = None
        try:
            adapter = self._adapter(store, http, flyers=True)
            sources = await adapter.fetch_flyers()
            processor = self.flyer_processor()
            for src in sources:
                content, mime = await self._download(http, src.url)
                results.append(processor.process(store, content, mime or src.mime_type, src, run_id))
            failed = [r for r in results if r.status == "failed"]
            if failed:
                alerts.append({"code": "flyer_failed", "severity": "critical",
                               "message": f"{len(failed)} flyer(s) failed: {failed[0].errors[:1]}"})
            if any(r.status == "needs_review" for r in results):
                alerts.append({"code": "flyer_review", "severity": "warning",
                               "message": "Some flyer offers need manual review"})
            status = "partial" if failed else "success"
        except Exception as e:
            log.exception("flyer run failed for %s", store.slug)
            alerts.append({"code": "run_crashed", "severity": "critical", "message": f"{type(e).__name__}: {e}"[:400]})
        finally:
            if adapter:
                await adapter.aclose()
            await http.aclose()
            stats = {"items_seen": sum(r.extracted for r in results),
                     "items_upserted": sum(r.accepted + r.pending_review for r in results),
                     "matched_products": sum(r.matched for r in results),
                     "flyers": len(results), "skipped": sum(r.skipped for r in results),
                     "accepted": sum(r.accepted for r in results),
                     "pending_review": sum(r.pending_review for r in results),
                     "rejected": sum(r.rejected for r in results), "expired": sum(r.expired for r in results),
                     "llm_calls": getattr(self.vision, "calls", None)}
            self.repo.finish_run(run_id, status, stats, alerts, [], None)
        if alerts:
            self.notifier.send(f"[{status.upper()}] {store.name_en} flyer run #{run_id}",
                               [f"{a['severity']}: {a['message']}" for a in alerts])
        return RunSummary(store.slug, "flyer", status, run_id, stats, alerts)

    def flyer_processor(self) -> FlyerProcessor:
        assert self.vision is not None
        return FlyerProcessor(self.vision, self.repo, Normalizer(self.repo.latest_usd_lbp()),
                              ProductMatcher(self.repo.catalog_index()), today=self.today)

    @staticmethod
    async def _download(http: PoliteHttpClient, url: str) -> tuple[bytes, str | None]:
        if url.startswith("file://"):
            path = Path(url2pathname(urlsplit(url).path))
            return path.read_bytes(), mimetypes.guess_type(path.name)[0]
        return await http.get_bytes(url)
