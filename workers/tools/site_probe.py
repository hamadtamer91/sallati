"""Read-only reconnaissance before writing a store adapter.

For each site: robots.txt, terms-of-use clauses about automated/commercial use, platform
fingerprint, which public data endpoints answer, sitemap. Every path is checked against
robots.txt first (wildcard-aware) and skipped if disallowed. ~10 requests per site.
Usage: python site_probe.py https://a.com https://b.com ...
"""
import re
import sys
import time
from urllib.parse import urljoin, urlsplit

import httpx

from pricewatch.pipeline.robots import Robots

UA = "SallatiPriceBot/0.1 (price comparison for Lebanon)"
CLAUSE = re.compile(
    r"[^.]{0,200}(scrap|robot|crawl|automat|spider|data.?min|harvest|extract|reproduc|copy|commercial|"
    r"mirror|frame|aggregat|استخراج|نسخ|تجاري)[^.]{0,200}\.", re.I)
TERMS_LINK = re.compile(r'href="([^"]*(?:terms|condition|legal|disclaimer|terms-of-use|شروط)[^"]*)"', re.I)
FINGERPRINTS = {
    "magento": ("Magento_", "mage/cookies", "/static/version"),
    "shopify": ("cdn.shopify.com", "Shopify.theme"),
    "woocommerce": ("woocommerce", "wp-content"),
    "nextjs": ("__NEXT_DATA__", "/_next/static"),
    "sap-hybris": ("/_ui/", "ACC.config", "hybris"),
    "nuxt": ("__NUXT__", "/_nuxt/"),
    "angular": ("ng-version",),
    "wordpress": ("wp-json",),
}


def text_of(html: str) -> str:
    html = re.sub(r"(?s)<(script|style).*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def survey(base: str) -> None:
    base = base.rstrip("/")
    print(f"\n\n################ {base} ################", flush=True)
    c = httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en,ar"}, timeout=30, follow_redirects=True)
    try:
        r = c.get(base + "/robots.txt")
        robots_txt = r.text if r.status_code < 400 and "<html" not in r.text[:200].lower() else ""
    except httpx.HTTPError as e:
        print("robots.txt ERROR", repr(e))
        return
    print(f"== robots.txt HTTP {r.status_code}\n{robots_txt[:1500] or '(none)'}")
    rules = Robots(robots_txt, UA)

    def get(path: str, **kw):
        url = path if path.startswith("http") else urljoin(base + "/", path.lstrip("/"))
        if not rules.allowed(url):
            print(f"== {url}: SKIPPED (robots.txt)")
            return None
        time.sleep(2)
        try:
            return c.request(kw.pop("method", "GET"), url, **kw)
        except httpx.HTTPError as e:
            print(f"== {url}: ERROR {e!r}")
            return None

    home = get("/")
    if home is None:
        return
    html = home.text
    final = str(home.url)
    print(f"== home HTTP {home.status_code} final={final} bytes={len(html)} "
          f"challenge={'cf-chl' in html or 'captcha' in html.lower() or 'just a moment' in html.lower()}")
    print("== platform:", [k for k, sigs in FINGERPRINTS.items() if any(s in html for s in sigs)] or "unknown")
    print("== prices on home:", re.findall(r"(?:\$\s?\d+[.,]\d{2}|LBP\s?[\d,.]+|[\d,.]+\s?(?:LBP|ل\.ل))", html)[:6])

    links = []
    for href in TERMS_LINK.findall(html):
        u = urljoin(final, href)
        if urlsplit(u).netloc == urlsplit(final).netloc and u not in links:
            links.append(u)
    print("== terms links:", links[:6] or "none found on home page")
    for u in links[:3]:
        r = get(u)
        if r is not None:
            t = text_of(r.text)
            hits = [m.group(0).strip() for m in CLAUSE.finditer(t)][:8]
            print(f"== terms {u} HTTP {r.status_code} chars={len(t)}")
            for h in hits:
                print("   -", h[:400])

    host = urlsplit(final).scheme + "://" + urlsplit(final).netloc
    checks = [
        ("shopify products.json", "GET", f"{host}/products.json?limit=1", None),
        ("woo store api", "GET", f"{host}/wp-json/wc/store/v1/products?per_page=1", None),
        ("magento graphql", "POST", f"{host}/graphql",
         {"query": "{ products(search: \"milk\", pageSize: 1) { total_count items { name sku } } }"}),
    ]
    for name, method, url, body in checks:
        r = get(url, method=method, json=body) if body else get(url)
        if r is not None:
            print(f"== {name}: HTTP {r.status_code} {r.headers.get('content-type', '')[:40]} :: {r.text[:300]!r}")

    for sm in re.findall(r"(?im)^sitemap:\s*(\S+)", robots_txt)[:2] or [f"{host}/sitemap.xml"]:
        r = get(sm)
        if r is not None:
            print(f"== sitemap {sm}: HTTP {r.status_code} :: {r.text[:400]!r}")


if __name__ == "__main__" and sys.argv[1:2] not in (["--pages"], ["--save"]):
    for site in sys.argv[1:]:
        try:
            survey(site)
        except Exception as e:  # keep going with the other sites
            print(f"!! {site} failed: {e!r}")


def pages(urls: list[str]) -> None:
    """Second pass: read specific pages (terms text, a listing page's price structure)."""
    c = httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en,ar"}, timeout=40, follow_redirects=True)
    robots: dict[str, Robots] = {}
    for url in urls:
        host = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        if host not in robots:
            try:
                r = c.get(host + "/robots.txt")
                robots[host] = Robots(r.text if r.status_code < 400 else "", UA)
            except httpx.HTTPError as e:
                print(f"\n#### {url}: robots ERROR {e!r}")
                continue
        if not robots[host].allowed(url):
            print(f"\n#### {url}: SKIPPED (robots.txt)")
            continue
        time.sleep(2)
        try:
            r = c.get(url)
        except httpx.HTTPError as e:
            print(f"\n#### {url}: ERROR {e!r}")
            continue
        html = r.text
        print(f"\n#### {url} HTTP {r.status_code} bytes={len(html)}")
        if url.endswith(".xml"):
            locs = re.findall(r"<loc>([^<]+)</loc>", html)
            print(f"   {len(locs)} urls; samples:", locs[:3], [u for u in locs if re.search(r"term|condition|legal|polic", u, re.I)][:5])
            pats = {}
            for u in locs:
                k = re.sub(r"/[^/]+$", "/…", urlsplit(u).path)
                pats[k] = pats.get(k, 0) + 1
            print("   path patterns:", sorted(pats.items(), key=lambda x: -x[1])[:8])
            continue
        if re.search(r"term|condition|legal|polic", url, re.I):
            t = text_of(html)
            print(f"   text chars={len(t)}")
            for m in list(CLAUSE.finditer(t))[:12]:
                print("   -", m.group(0).strip()[:420])
            continue
        nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        print("   __NEXT_DATA__:", len(nd.group(1)) if nd else None,
              "| price-ish json keys:", sorted(set(re.findall(r'"(\w*[Pp]rice\w*)"\s*:', html)))[:12])
        i = min([p for p in (html.find("$"), html.find("LBP"), html.find("ل.ل")) if p > 0] or [0])
        print("   around first price:", re.sub(r"\s+", " ", html[max(0, i - 700): i + 300]))


if __name__ == "__main__" and sys.argv[1:2] == ["--pages"]:
    pages(sys.argv[2:])
    sys.exit(0)


def save(folder: str, urls: list[str]) -> None:
    """Save raw pages (robots-checked, 3 s apart) so they can be studied offline."""
    import pathlib
    out = pathlib.Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    c = httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en,ar"}, timeout=40, follow_redirects=True)
    robots: dict[str, Robots] = {}
    for i, url in enumerate(urls):
        host = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        if host not in robots:
            r = c.get(host + "/robots.txt")
            robots[host] = Robots(r.text if r.status_code < 400 else "", UA)
        if not robots[host].allowed(url):
            print("SKIPPED (robots)", url)
            continue
        time.sleep(3)
        r = c.get(url)
        name = f"{i:02d}_" + re.sub(r"[^A-Za-z0-9]+", "_", urlsplit(url).path + "_" + urlsplit(url).query)[:80] + ".html"
        (out / name).write_text(r.text, encoding="utf-8")
        print(r.status_code, len(r.text), url, "->", name)


if __name__ == "__main__" and sys.argv[1:2] == ["--save"]:
    save(sys.argv[2], sys.argv[3:])
    sys.exit(0)
