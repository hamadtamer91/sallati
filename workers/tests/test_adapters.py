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
