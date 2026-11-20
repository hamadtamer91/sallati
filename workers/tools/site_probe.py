"""One-off reconnaissance for a new store: robots.txt, terms, and which data access works.
Respects robots.txt: any path it disallows is skipped. Very low request count.
Usage: python site_probe.py https://www.example.com [magento_store_code]
"""
import re
import sys
import time
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

BASE = sys.argv[1].rstrip("/")
STORE = sys.argv[2] if len(sys.argv) > 2 else "default"
UA = "SallatiPriceBot/0.1 (price comparison for Lebanon)"
c = httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en,ar"}, timeout=40, follow_redirects=True)
rp = RobotFileParser()


def show(title, body):
    print(f"\n===== {title} =====\n{body}", flush=True)


def get(path, **kw):
    url = urljoin(BASE + "/", path.lstrip("/"))
    if not rp.can_fetch(UA, url):
        show(path, "SKIPPED: disallowed by robots.txt")
        return None
    time.sleep(2)
    try:
        return c.request(kw.pop("method", "GET"), url, **kw)
    except httpx.HTTPError as e:
        show(path, f"ERROR {e!r}")
        return None


r = c.get(BASE + "/robots.txt")
show("robots.txt", f"HTTP {r.status_code}\n{r.text[:4000]}")
rp.parse(r.text.splitlines() if r.status_code < 400 else [])

for path in (f"/{STORE}/terms-of-use/", f"/{STORE}/disclaimer/"):
    r = get(path)
    if r is not None:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<(script|style).*?</\1>", " ", r.text)))
        hits = [m.group(0).strip() for m in re.finditer(
            r"[^.]{0,220}(scrap|robot|crawl|automat|spider|data.?min|harvest|extract|reproduc|copy|commercial)[^.]{0,220}\.",
            text, re.I)]
        show(f"terms {path} HTTP {r.status_code} chars={len(text)}", "\n- ".join([""] + hits[:20]) or "(no matching clauses)")

q = ('{ products(search: "milk", pageSize: 3) { total_count page_info { total_pages } items { sku name '
     'url_key stock_status price_range { minimum_price { regular_price { value currency } '
     'final_price { value currency } } } } } }')
for ep in ("/graphql", f"/{STORE}/graphql"):
    r = get(ep, method="POST", json={"query": q}, headers={"Store": STORE, "Content-Type": "application/json"})
    if r is not None:
        show(f"graphql POST {ep}", f"HTTP {r.status_code} {r.headers.get('content-type')}\n{r.text[:1800]}")

r = get(f"/rest/{STORE}/V1/products?searchCriteria[pageSize]=2")
if r is not None:
    show("rest products", f"HTTP {r.status_code}\n{r.text[:800]}")

for p in ("/sitemap.xml", "/pub/media/sitemap.xml", "/media/sitemap.xml", f"/{STORE}/sitemap.xml"):
    r = get(p)
    if r is not None:
        show(f"sitemap {p}", f"HTTP {r.status_code} {r.headers.get('content-type')}\n{r.text[:600]}")

r = get(f"/{STORE}/promotions.html?product_list_limit=36")
if r is not None:
    html = r.text
    show("category page /promotions.html", f"HTTP {r.status_code} bytes={len(html)} "
         f"product-item={html.count('product-item-info')} data-price-amount={html.count('data-price-amount')} "
         f"old-price={html.count('old-price')} cloudflare={'cf-' in html.lower()}")
    i = html.find("product-item-info")
    show("first product block", html[max(0, i - 200): i + 2500] if i >= 0 else html[:1500])
    for pat in (r'"currencyCode"\s*:\s*"(\w+)"', r'data-price-type="(\w+)"', r'"priceFormat"\s*:\s*\{[^}]{0,200}\}',
                r'class="toolbar-number">(\d+)</span>', r'href="([^"]+promotions[^"]*\?p=2[^"]*)"'):
        show(f"pattern {pat}", re.findall(pat, html)[:5])
