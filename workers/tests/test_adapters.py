import json
from pathlib import Path

import httpx
import pytest

from pricewatch.adapters.base import AdapterContext
from pricewatch.adapters.registry import AdapterError, autoload, available_adapters, build_adapter, register_adapter
from pricewatch.adapters.base import StoreAdapter
from pricewatch.pipeline.http import PoliteHttpClient, RobotsDisallowed

PLUGINS = str(Path(__file__).resolve().parents[1] / "plugins")


def client(handler) -> PoliteHttpClient:
    return PoliteHttpClient(rate_per_sec=1000, burst=100, respect_robots=False,
                            transport=httpx.MockTransport(handler))


async def collect(adapter):
    return [x async for x in adapter.iter_listings()]


def test_registry_discovers_core_and_plugins():
    autoload(force=True, plugin_paths=[PLUGINS])
    keys = available_adapters()
    for k in ("shopify", "woocommerce_store_api", "json_api", "html_css", "flyer_links", "demo_fixture",
              "example_lb_app_api"):
        assert k in keys


def test_registry_rejects_duplicates_and_bad_config():
    with pytest.raises(AdapterError):
        @register_adapter("shopify")
        class Other(StoreAdapter):  # noqa: F811
            pass
    with pytest.raises(AdapterError, match="invalid config"):
        build_adapter("shopify", AdapterContext("x", {"base_url": "https://a", "typo_key": 1}, client(None)))
    with pytest.raises(AdapterError, match="unknown adapter"):
        build_adapter("nope", AdapterContext("x", {}, client(None)))


def test_env_placeholders(monkeypatch):
    monkeypatch.setenv("TOKEN_X", "s3cret")
    a = build_adapter("json_api", AdapterContext("x", {
        "url": "https://a/p", "headers": {"Authorization": "Bearer ${TOKEN_X}"},
        "fields": {"external_id": "id", "name": "n", "price": "p"}}, client(None)))
    assert a.cfg.headers["Authorization"] == "Bearer s3cret"


async def test_shopify():
    def handler(req: httpx.Request):
        page = int(req.url.params["page"])
        products = [] if page > 1 else [{
            "title": "Coca-Cola", "vendor": "Coca-Cola", "handle": "coke", "product_type": "Soft Drinks",
            "images": [{"src": "https://img/1.jpg"}],
            "variants": [{"id": 11, "title": "6 x 330ml", "price": "3.20", "compare_at_price": "3.60",
                          "available": True, "barcode": "5449000000125"}]}]
        return httpx.Response(200, json={"products": products})
    a = build_adapter("shopify", AdapterContext("s", {"base_url": "https://shop.lb"}, client(handler)))
    items = await collect(a)
    assert len(items) == 1 and items[0].name == "Coca-Cola 6 x 330ml" and items[0].size_text == "6 x 330ml"


async def test_woocommerce_minor_units_and_pages():
    def handler(req):
        page = int(req.url.params["page"])
        item = {"id": page, "name": "Rice &amp; Beans 1kg", "is_in_stock": True, "sku": "",
                "prices": {"price": "245", "regular_price": "300", "currency_code": "USD", "currency_minor_unit": 2}}
        return httpx.Response(200, json=[item], headers={"X-WP-TotalPages": "2"})
    a = build_adapter("woocommerce_store_api", AdapterContext("w", {"base_url": "https://w.lb"}, client(handler)))
    items = await collect(a)
    assert [i.external_id for i in items] == ["1", "2"]
    assert str(items[0].price) == "2.45" and items[0].name == "Rice & Beans 1kg"


async def test_json_api_branch_and_cursor():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        cur = req.url.params.get("cursor")
        data = {"data": {"items": [{"id": 1 if not cur else 2, "t": {"ar": "سكر ١ كغ"}, "p": 95000}]},
                "next": None if cur else "abc"}
        return httpx.Response(200, json=data)
    cfg = {"url": "https://api.lb/b/{branch}/products", "items_path": "data.items", "currency": "LBP",
           "pagination": {"type": "cursor", "param": "cursor", "cursor_path": "next", "size_param": None},
           "fields": {"external_id": "id", "name": "t.ar", "price": "p"}, "branch_placeholder_values": ["7"]}
    a = build_adapter("json_api", AdapterContext("j", cfg, client(handler)))
    items = await collect(a)
    assert [i.external_id for i in items] == ["1", "2"] and items[0].branch_external_id == "7"
    assert "/b/7/products" in calls[0]


async def test_html_css_static():
    html = """<html><body>
      <div class="card"><a class="l" href="/p/1"><h3>Tannourine Water 6x1.5L</h3></a>
        <span class="now">$2.10</span><span class="old">$2.50</span></div>
      <div class="card"><a class="l" href="/p/2"><h3>Puck Cream 170g</h3></a>
        <span class="now">1.35$</span><span class="stock">غير متوفر</span></div>
      <a class="next" href="/groceries?page=2">next</a></body></html>"""
    page2 = "<html><body></body></html>"

    def handler(req):
        return httpx.Response(200, text=page2 if "page=2" in str(req.url) else html)
    cfg = {"start_urls": ["https://shop.lb/groceries"], "item_selector": "div.card",
           "fields": {"name": "h3", "price": "span.now", "regular_price": "span.old", "url": "a.l@href",
                      "stock": "span.stock"}, "next_page_selector": "a.next@href"}
    a = build_adapter("html_css", AdapterContext("h", cfg, client(handler)))
    items = await collect(a)
    assert [i.external_id for i in items] == ["https://shop.lb/p/1", "https://shop.lb/p/2"]
    assert items[0].in_stock and not items[1].in_stock


async def test_flyer_links():
    html = '<a href="/f/week47.pdf">PDF</a><img src="/img/logo.png"><img data-src="https://cdn.lb/flyer-p2.jpg">'
    a = build_adapter("flyer_links", AdapterContext("f", {"page_url": "https://s.lb/offers"},
                                                    client(lambda r: httpx.Response(200, text=html))))
    urls = [f.url for f in await a.fetch_flyers()]
    assert urls == ["https://s.lb/f/week47.pdf", "https://cdn.lb/flyer-p2.jpg"]


async def test_plugin_adapter_works_without_core_changes():
    autoload(force=True, plugin_paths=[PLUGINS])

    def handler(req):
        if req.url.path == "/zones":
            return httpx.Response(200, json={"zones": [{"id": "beirut-1", "label": "Beirut", "center": [33.89, 35.50]}]})
        return httpx.Response(200, json={"products": [{"sku": 9, "name_ar": "زيت عافية", "name_en": None,
                                                      "variant": {"qty": "1.8", "unit": "L"}, "price_lbp": 650000,
                                                      "stock": 3}], "next": None})
    a = build_adapter("example_lb_app_api", AdapterContext(
        "app", {"api_base": "https://api.app.lb", "zone_ids": ["beirut-1"]}, client(handler)))
    assert (await a.fetch_branches())[0].external_id == "beirut-1"
    items = await collect(a)
    assert items[0].size_text == "1.8 L" and items[0].currency.value == "LBP"


async def test_robots_and_retry():
    hits = {"n": 0}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        hits["n"] += 1
        return httpx.Response(503) if hits["n"] == 1 else httpx.Response(200, json={"ok": True})
    http = PoliteHttpClient(rate_per_sec=1000, burst=100, transport=httpx.MockTransport(handler))
    import pricewatch.pipeline.http as h
    orig = h.asyncio.sleep

    async def fast_sleep(_):
        await orig(0)
    h.asyncio.sleep = fast_sleep
    try:
        assert await http.get_json("https://s.lb/api") == {"ok": True}
        assert hits["n"] == 2
        with pytest.raises(RobotsDisallowed):
            await http.get_text("https://s.lb/private/x")
    finally:
        h.asyncio.sleep = orig


def test_robots_wildcards_like_spinneys():
    from pricewatch.pipeline.robots import Robots

    txt = "User-agent: *\nDisallow: /index.php/\nDisallow: /*?*\nDisallow: /*.php$\nDisallow: /catalog/\n" \
          "Allow: /catalog/public$\n\nUser-agent: BadBot\nDisallow: /\n"
    r = Robots(txt, "SallatiPriceBot/0.1 (+x)")
    assert r.allowed("https://s.lb/default/promotions.html")
    assert not r.allowed("https://s.lb/default/promotions.html?p=2")          # query strings blocked
    assert not r.allowed("https://s.lb/default/promotions.html?product_list_limit=36")
    assert not r.allowed("https://s.lb/x/info.php") and r.allowed("https://s.lb/x/info.php5")
    assert not r.allowed("https://s.lb/catalog/abc") and r.allowed("https://s.lb/catalog/public")
    assert not Robots(txt, "BadBot/1.0").allowed("https://s.lb/anything")
    assert Robots("", "x").allowed("https://s.lb/a?b")


async def test_le_charcutier_adapter_on_real_saved_pages():
    autoload(force=True, plugin_paths=[PLUGINS])
    fx = Path(__file__).parent / "fixtures" / "le_charcutier"
    seen_posts = []

    def handler(req: httpx.Request):
        path = req.url.path
        if req.method == "GET":
            return httpx.Response(200, text=(fx / "category_page.html").read_text())
        seen_posts.append(path)
        assert req.headers.get("X-CSRF-TOKEN")                      # token taken from the page
        if path == "/our_branches_ajax":
            return httpx.Response(200, text=(fx / "branches.json").read_text())
        if path == "/shop_filter":
            body = req.content.decode()
            return httpx.Response(200, text=(fx / ("list_p2.json" if "offset=3" in body else "list_p1.json")).read_text())
        if path.startswith("/get_product_details/"):
            pid = path.split("/")[2]
            f = fx / f"get_product_details_{pid}_13.json"
            return httpx.Response(200, text=f.read_text() if f.exists() else "<div>no price</div>")
        return httpx.Response(404)

    a = build_adapter("le_charcutier", AdapterContext(
        "lc", {"departments": ["grocery-foods"]}, client(handler)))
    branches = await a.fetch_branches()
    names = {b.external_id for b in branches}
    assert len(branches) == 17                                      # 19 listed, 2 bad pins skipped
    assert "monteverde" not in names and "fanar" not in names
    ash = next(b for b in branches if b.external_id == "ashrafieh")
    assert (round(ash.lat, 3), round(ash.lng, 3), ash.region_slug) == (33.892, 35.518, "beirut")

    items = [x async for x in a.iter_listings()]
    by_id = {i.external_id: i for i in items}
    assert set(by_id) == {"101306", "105563"}                       # others had no saved price
    rice = by_id["101306"]
    assert (rice.name, rice.brand, rice.size_text, rice.price) == ("EGYPTIAN RICE", "Deroni", "5Kg", "LBP 531,024")
    assert rice.regular_price is None and rice.in_stock             # "LBP 0" strike = no discount
    assert seen_posts.count("/shop_filter") == 2                    # paginated by offset
    assert a.skipped_no_price == 3                                  # 103035 appears twice but is counted once


async def test_le_charcutier_skips_a_broken_product_and_stops_when_site_is_down(monkeypatch):
    import sys
    autoload(force=True, plugin_paths=[PLUGINS])
    fx = Path(__file__).parent / "fixtures" / "le_charcutier"
    down = {"all": False}

    def handler(req: httpx.Request):
        path = req.url.path
        if req.method == "GET":
            return httpx.Response(200, text=(fx / "category_page.html").read_text())
        if path == "/shop_filter":
            body = req.content.decode()
            return httpx.Response(200, text=(fx / ("list_p2.json" if "offset=3" in body else "list_p1.json")).read_text())
        if path.startswith("/get_product_details/"):
            pid = path.split("/")[2]
            if down["all"] or pid == "101306":                       # same error the live run hit
                return httpx.Response(507)
            f = fx / f"get_product_details_{pid}_13.json"
            return httpx.Response(200, text=f.read_text() if f.exists() else "<div>no price</div>")
        return httpx.Response(404)

    def fast_client():
        return PoliteHttpClient(rate_per_sec=1000, burst=100, respect_robots=False, max_retries=1,
                                transport=httpx.MockTransport(handler))

    async def no_sleep(_):
        return None
    monkeypatch.setattr("pricewatch.pipeline.http.asyncio.sleep", no_sleep)

    a = build_adapter("le_charcutier", AdapterContext("lc", {"departments": ["grocery-foods"]}, fast_client()))
    items = [x async for x in a.iter_listings()]
    assert {i.external_id for i in items} == {"105563"}            # the broken one is skipped, the rest kept
    assert a.failed_requests == 1

    down["all"] = True
    b = build_adapter("le_charcutier", AdapterContext("lc", {"departments": ["grocery-foods"]}, fast_client()))
    monkeypatch.setattr(sys.modules[type(b).__module__], "MAX_CONSECUTIVE_ERRORS", 3)
    with pytest.raises(RuntimeError, match="site looks down"):
        [x async for x in b.iter_listings()]


def test_le_charcutier_prices_normalise():
    from pricewatch.core.normalize import Normalizer
    from pricewatch.core.models import RawListing
    from decimal import Decimal
    n = Normalizer(Decimal("89700")).normalize_listing(RawListing(
        external_id="1", name="EGYPTIAN RICE", brand="Deroni", size_text="5Kg", price="LBP 531,024", currency="LBP"))
    assert n.price_usd == Decimal("5.9200") and n.quantity.total == Decimal("5000") and n.unit_price_basis == "kg"
