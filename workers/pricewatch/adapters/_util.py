from __future__ import annotations

import html
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node


def pluck(obj: Any, path: str | None, default: Any = None) -> Any:
    """Dotted path getter: pluck(d, "prices.price"), pluck(d, "images.0.src")."""
    if not path:
        return default
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            i = int(part)
            cur = cur[i] if -len(cur) <= i < len(cur) else None
        else:
            return default
        if cur is None:
            return default
    return cur


def clean(text: Any) -> str | None:
    if text is None:
        return None
    s = html.unescape(str(text)).strip()
    return s or None


def minor_units(value: str | int | None, minor: int) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value)) / (Decimal(10) ** minor)


def css_value(node: Node, selector: str, base_url: str = "") -> str | None:
    """'h2.title' -> text ; 'a.title@href' -> attribute (made absolute for href/src)."""
    sel, _, attr = selector.partition("@")
    target = node.css_first(sel) if sel else node
    if target is None:
        return None
    if attr:
        val = target.attributes.get(attr)
        if val and attr in {"href", "src", "data-src"} and base_url:
            val = urljoin(base_url, val)
        return clean(val)
    return clean(target.text(separator=" ", strip=True))


def parse_html(text: str) -> HTMLParser:
    return HTMLParser(text)
