import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml
from PIL import Image

from pricewatch.core.matching import ProductMatcher
from pricewatch.core.models import FlyerSource, RawPromotion
from pricewatch.core.normalize import Normalizer
from pricewatch.pipeline.health import RunStats, evaluate
from pricewatch.pipeline.notify import LogNotifier
from pricewatch.pipeline.runner import Runner
from pricewatch.storage.memory import MemoryRepository
from pricewatch.vision.client import FakeVisionLLM
from pricewatch.vision.processor import FlyerProcessor
from pricewatch.vision.tiling import make_tiles, render_document
from pricewatch.vision.validator import PromotionValidator, parse_date

STORES = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "stores.yaml").read_text())["stores"]
TODAY = date(2026, 11, 19)


def demo_repo() -> MemoryRepository:
    repo = MemoryRepository(Decimal("89700"))
    for s in STORES:
        repo.upsert_store(s)
    return repo


async def test_catalog_run_matches_across_stores_and_languages():
    repo, notifier = demo_repo(), LogNotifier()
    runner = Runner(repo, notifier)
    cedar = await runner.run_catalog(repo.get_store("demo-cedar-mart"))
    bayt = await runner.run_catalog(repo.get_store("demo-bayt-market"))

    assert cedar.status == "success" and cedar.stats["items_failed"] == 1   # "call us" price
    assert bayt.status == "success" and bayt.stats["items_failed"] == 0
    # Nido (barcode) and Coca-Cola 6-pack (attributes, Arabic vs English) are shared canonicals
    by_name = {sp["name"]: sp for sp in repo.source_products.values()}
    assert by_name["حليب نيدو بودرة ٩٠٠ غ"]["canonical_id"] == by_name["Nido Fortified Milk Powder 900g"]["canonical_id"]
    assert by_name["حليب نيدو بودرة ٩٠٠ غ"]["method"] == "barcode"
    six = by_name["كوكا كولا ٦ × ٣٣٠ مل"]["canonical_id"]
    assert six == by_name["Coca-Cola Original 6x330ml"]["canonical_id"]
    assert six != by_name["Coca-Cola Can 330ml"]["canonical_id"]           # never the single can
    # branch pricing kept separately
    nido_offers = [o for (sp, b), o in repo.offers.items() if sp == by_name["Nido Fortified Milk Powder 900g"]["id"]]
    assert sorted(o["price_usd"] for o in nido_offers) == [Decimal("9.45"), Decimal("9.75")]
    # LBP converted with the stored rate
    lbp = [o for (sp, b), o in repo.offers.items() if sp == by_name["حليب نيدو بودرة ٩٠٠ غ"]["id"]][0]
    assert lbp["price_usd"] == Decimal("9.4203")


async def test_drop_in_items_keeps_old_prices_and_alerts():
    repo, notifier = demo_repo(), LogNotifier()
    runner = Runner(repo, notifier)
    store = repo.get_store("demo-bayt-market")
    for _ in range(3):
        await runner.run_catalog(store)
    store.adapter_config["limit"] = 2          # simulate a site that suddenly shows 2 products
    s = await runner.run_catalog(store)
    assert s.status == "partial" and s.alerts[0]["code"] == "item_count_drop"
    assert not any(o["stale"] for o in repo.offers.values())               # nothing wiped
    assert notifier.sent and "PARTIAL" in notifier.sent[-1][0]


def test_health_rules():
    st = RunStats(items_seen=100, items_failed=30)
    st.failure_reasons["price"] = 30
    alerts, baseline = evaluate(st, [100, 105, 98])
    assert baseline == 100 and {a.code for a in alerts} == {"schema_drift"}


def test_dates_and_validator():
    assert parse_date("12/11", TODAY) == date(2026, 11, 12)
    assert parse_date("٢٥/١٢/٢٠٢٦", TODAY) == date(2026, 12, 25)
    assert parse_date("03/01", TODAY) == date(2027, 1, 3)   # early January = next year
    v = PromotionValidator(TODAY, Decimal("89700"))
    ok = v.validate(RawPromotion(name="Puck cream 170g", price=1.25, regular_price=1.60, currency="USD",
                                 confidence=0.9), date(2026, 11, 17), date(2026, 11, 30))
    assert ok.status == "active" and ok.promo_price_usd == Decimal("1.2500")
    old = v.validate(RawPromotion(name="x item", price=2, valid_from="2026-10-01", valid_until="2026-10-07",
                                  confidence=0.9))
    assert old.status == "expired"
    nodates = v.validate(RawPromotion(name="Rice 1kg", price=1.5, confidence=0.95))
    assert nodates.status == "pending_review" and nodates.dates_inferred
    bad = v.validate(RawPromotion(name="Oil 1L", promo_type="percent_off", confidence=0.9), date(2026, 11, 17),
                     date(2026, 11, 30))
    assert bad.status == "rejected"
    multi = v.validate(RawPromotion(name="Pepsi can", promo_type="multibuy", price=2, buy_qty=3,
                                    regular_price=0.9, confidence=0.9), date(2026, 11, 17), date(2026, 11, 30))
    assert multi.status == "active" and multi.buy_qty == 3


def test_tiling_covers_page_with_overlap():
    img = Image.new("RGB", (2400, 3300), "white")
    tiles = make_tiles(img, tile_px=1100, overlap=0.15)
    assert len(tiles) == 3 * 4
    assert tiles[-1].box[2] == 2400 and tiles[-1].box[3] == 3300
    xs = sorted({t.box[0] for t in tiles})
    assert xs[1] < 1100  # overlap between neighbours


def _pdf_bytes() -> bytes:
    pages = [Image.new("RGB", (1240, 1754), "white") for _ in range(2)]
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=150)
    return buf.getvalue()


async def test_flyer_processing_end_to_end_with_fake_vision():
    repo = demo_repo()
    await Runner(repo).run_catalog(repo.get_store("demo-cedar-mart"))   # gives Puck/Nido canonicals & prices
    store = repo.get_store("demo-cedar-mart")

    def responder(instruction, schema, image):
        if "first page" in instruction:
            return {"store_name": "Cedar Mart", "flyer_valid_from": "17/11", "flyer_valid_until": "30/11",
                    "default_currency": "USD"}
        item = {"name": "Nido Fortified Milk Powder", "brand": "Nido", "size_text": "900g", "price": 8.99,
                "regular_price": 9.75, "currency": "USD", "promo_type": "price_cut", "percent_off": None,
                "buy_qty": None, "get_qty": None, "valid_from": None, "valid_until": None, "confidence": 0.93}
        unknown = {**item, "name": "Mystery Biscuits", "brand": None, "size_text": "200g", "price": 1.1,
                   "regular_price": None, "confidence": 0.8}
        return {"flyer_valid_from": None, "flyer_valid_until": None, "items": [item, unknown]}

    llm = FakeVisionLLM(responder)
    proc = FlyerProcessor(llm, repo, Normalizer(Decimal("89700")), ProductMatcher(repo.catalog_index()), today=TODAY)
    content = _pdf_bytes()
    assert len(render_document(content)) == 2
    res = proc.process(store, content, "application/pdf", FlyerSource(url="file:///x.pdf"))
    assert res.pages == 2 and res.tiles == 8
    assert res.extracted == 2                              # same offers on every tile/page -> de-duplicated
    assert res.matched == 1 and res.accepted == 2
    promos = list(repo.promotions.values())
    nido = [p for p in promos if p["title"].startswith("Nido")]
    assert nido[0]["canonical_id"] and nido[0]["valid_until"] == date(2026, 11, 30)
    assert all(p["canonical_id"] is None for p in promos if p["title"].startswith("Mystery"))
    again = proc.process(store, content, "application/pdf", FlyerSource(url="file:///x.pdf"))
    assert again.skipped                                   # same file hash -> no second LLM bill
