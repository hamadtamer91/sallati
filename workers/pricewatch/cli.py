"""Command line entry point.

  pricewatch adapters                          list installed adapters
  pricewatch stores sync                       copy config/stores.yaml into the database
  pricewatch try --store SLUG --limit 20       dry run a store (no database) — use when adding a store
  pricewatch run catalog --all | --store SLUG  scrape prices
  pricewatch run flyers  --all | --store SLUG  fetch + read new flyers with the vision model
  pricewatch flyer --store SLUG --file x.pdf   read one flyer file
  pricewatch expire                            expire finished promotions
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

import yaml

from .adapters.registry import available_adapters
from .core.models import FlyerSource
from .pipeline.notify import from_env
from .pipeline.runner import Runner
from .storage.memory import MemoryRepository

WORKERS_DIR = Path(__file__).resolve().parents[1]
DEFAULT_STORES = WORKERS_DIR / "config" / "stores.yaml"


def _load_yaml(path: Path) -> list[dict]:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("stores", [])


def _repo():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL is not set")
    from .storage.postgres import PostgresRepository

    return PostgresRepository(dsn)


def _vision():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    from .vision.client import AnthropicVisionLLM

    return AnthropicVisionLLM()


def _stores(repo, args) -> list:
    if args.store:
        s = repo.get_store(args.store)
        if not s:
            sys.exit(f"unknown store {args.store}")
        return [s]
    return repo.list_stores()


def cmd_adapters(_args) -> None:
    for key, cls in available_adapters().items():
        caps = cls.capabilities
        flags = [k for k in ("catalog", "branches", "branch_pricing", "flyers") if getattr(caps, k)]
        print(f"{key:24} {cls.__module__:45} {', '.join(flags)}")


def cmd_sync(args) -> None:
    repo = _repo()
    for s in _load_yaml(Path(args.file)):
        repo.upsert_store(s)
        print("synced", s["slug"])


def cmd_try(args) -> None:
    """Run one store from the YAML against an in-memory DB and print what would be stored."""
    entry = next((s for s in _load_yaml(Path(args.file)) if s["slug"] == args.store), None)
    if not entry:
        sys.exit(f"{args.store} not in {args.file}")
    repo = MemoryRepository()
    repo.upsert_store({**entry, "active": True})
    store = repo.get_store(entry["slug"])
    summary = asyncio.run(Runner(repo).run_catalog(store, limit=args.limit))
    for sp in list(repo.source_products.values())[: args.limit]:
        offer = next((o for (sid, _), o in repo.offers.items() if sid == sp["id"]), {})
        print(f"{sp['name'][:50]:50} ${offer.get('price_usd')}  unit=${offer.get('unit_price_usd')}  "
              f"{sp['method']} {sp['confidence']}")
    print(json.dumps({"status": summary.status, "stats": summary.stats, "alerts": summary.alerts},
                     indent=2, ensure_ascii=False, default=str))


def cmd_run(args) -> None:
    repo = _repo()
    runner = Runner(repo, from_env(), vision=_vision())
    exit_code = 0
    for store in _stores(repo, args):
        if args.kind == "catalog":
            if not store.adapter_key or store.adapter_config.get("catalog") is False:
                continue
            summary = asyncio.run(runner.run_catalog(store, limit=args.limit))
        else:
            caps = available_adapters().get(store.adapter_key)
            if "flyers" not in store.adapter_config and not (caps and caps.capabilities.flyers):
                continue
            summary = asyncio.run(runner.run_flyers(store))
        print(f"{summary.store}: {summary.kind} {summary.status} {summary.stats.get('items_seen')} items")
        exit_code |= summary.status == "failed"
    sys.exit(exit_code)


def cmd_flyer(args) -> None:
    repo = _repo()
    vision = _vision()
    if vision is None:
        sys.exit("ANTHROPIC_API_KEY is not set")
    store = repo.get_store(args.store) or sys.exit(f"unknown store {args.store}")
    runner = Runner(repo, from_env(), vision=vision)
    path = Path(args.file)
    src = FlyerSource(url=path.resolve().as_uri(), title=path.name,
                      valid_from=date.fromisoformat(args.valid_from) if args.valid_from else None,
                      valid_until=date.fromisoformat(args.valid_until) if args.valid_until else None)
    res = runner.flyer_processor().process(store, path.read_bytes(), None, src)
    print(json.dumps(res.__dict__, indent=2, default=str))


def cmd_expire(_args) -> None:
    print("expired:", _repo().expire_promotions())


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="pricewatch")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("adapters").set_defaults(fn=cmd_adapters)

    st = sub.add_parser("stores").add_subparsers(dest="sub", required=True).add_parser("sync")
    st.add_argument("--file", default=str(DEFAULT_STORES))
    st.set_defaults(fn=cmd_sync)

    t = sub.add_parser("try")
    t.add_argument("--store", required=True)
    t.add_argument("--limit", type=int, default=20)
    t.add_argument("--file", default=str(DEFAULT_STORES))
    t.set_defaults(fn=cmd_try)

    r = sub.add_parser("run")
    r.add_argument("kind", choices=["catalog", "flyers"])
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--store")
    g.add_argument("--all", action="store_true")
    r.add_argument("--limit", type=int)
    r.set_defaults(fn=cmd_run)

    f = sub.add_parser("flyer")
    f.add_argument("--store", required=True)
    f.add_argument("--file", required=True)
    f.add_argument("--valid-from")
    f.add_argument("--valid-until")
    f.set_defaults(fn=cmd_flyer)

    sub.add_parser("expire").set_defaults(fn=cmd_expire)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
