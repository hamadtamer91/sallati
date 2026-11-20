"""Integration test against a real (migrated + seeded) database. Skipped unless TEST_DATABASE_URL is set."""
import os
from datetime import date
from decimal import Decimal

import pytest
import yaml

from pricewatch.core.models import FlyerSource
from pricewatch.core.text import normalize_text
from pricewatch.pipeline.runner import Runner
from pricewatch.vision.client import FakeVisionLLM
from tests.test_pipeline import STORES, _pdf_bytes

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def repo():
    from pricewatch.storage.postgres import PostgresRepository

    r = PostgresRepository(DSN)
    for t in ("promotions", "flyers", "price_history", "product_offers", "source_products",
              "canonical_products", "scraper_runs", "store_branches", "stores"):
        r.conn.execute(f"delete from {t}")
    for s in STORES:
        r.upsert_store({**s, "active": True})   # demo stores are inactive in stores.yaml
    yield r
    r.close()


async def test_full_flow_on_postgres(repo):
    runner = Runner(repo)
    for slug in ("demo-cedar-mart", "demo-bayt-market"):
        s = await runner.run_catalog(repo.get_store(slug))
        assert s.status == "success", s.alerts
    # second run: unchanged prices -> no new history rows
    hist_before = repo.conn.execute("select count(*) c from price_history").fetchone()["c"]
    await runner.run_catalog(repo.get_store("demo-bayt-market"))
    assert repo.conn.execute("select count(*) c from price_history").fetchone()["c"] == hist_before

    c = repo.conn
    # site discount (Almarai 1.95 instead of 2.25 on Cedar's site) shows on the explore page
    site = c.execute("select * from explore_deals() where source = 'site'").fetchall()
    assert any(r["title"].startswith("Almarai") and r["valid_until"] is None and r["discount_pct"] > 10 for r in site)
    nido = c.execute("select id from canonical_products where gtin='07613030000907'").fetchone()["id"]
    rows = c.execute("select * from offers_for_basket(%s, 33.8886, 35.5300, 10)", ([nido],)).fetchall()
    assert {r["branch_name_en"] for r in rows} == {"Cedar Mart Achrafieh", "Cedar Mart Jdeideh", "Bayt Market Dekwaneh"}
    jdd = next(r for r in rows if r["branch_name_en"] == "Cedar Mart Jdeideh")
    assert jdd["price_usd"] == Decimal("9.4500")             # branch-specific price wins

    found = c.execute("select * from search_products('حليب', 10)").fetchall()
    assert found and all(f["offers_count"] > 0 for f in found[:1])
    assert c.execute("select norm_text(%s) n", ("كوكا كولا ٦ × ٣٣٠ مل",)).fetchone()["n"] == \
        normalize_text("كوكا كولا ٦ × ٣٣٠ مل")

    # flyer -> promotions -> optimizer & explore
    def responder(instruction, schema, image):
        if "first page" in instruction:
            return {"store_name": None, "flyer_valid_from": "2026-11-17", "flyer_valid_until": "2026-11-30",
                    "default_currency": "USD"}
        return {"flyer_valid_from": None, "flyer_valid_until": None, "items": [{
            "name": "Nido Fortified Milk Powder", "brand": "Nido", "size_text": "900g", "price": 8.49,
            "regular_price": 9.75, "currency": "USD", "promo_type": "price_cut", "percent_off": None,
            "buy_qty": None, "get_qty": None, "valid_from": None, "valid_until": None, "confidence": 0.95}]}

    runner.vision = FakeVisionLLM(responder)
    runner.today = date.today()
    store = repo.get_store("demo-cedar-mart")
    res = runner.flyer_processor().process(store, _pdf_bytes(), "application/pdf", FlyerSource(url="file:///t.pdf"))
    if date.today() <= date(2026, 11, 30):
        assert res.accepted == 1, res
        rows = c.execute("select * from offers_for_basket(%s, 33.8886, 35.5300, 10)", ([nido],)).fetchall()
        ash = next(r for r in rows if r["branch_name_en"] == "Cedar Mart Achrafieh")
        assert ash["promo_type"] == "price_cut" and ash["promo_price_usd"] == Decimal("8.4900")
        deals = c.execute("select * from explore_deals('beirut', 'milk')").fetchall()
        assert deals and deals[0]["discount_pct"] > 10
