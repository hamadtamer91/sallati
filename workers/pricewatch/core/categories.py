"""Guess our category slug from the store's category label and the product name (keyword rules in YAML)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from .text import normalize_text

RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "category_keywords.yaml"


@lru_cache(maxsize=2)
def _rules(path: str | None = None) -> list[tuple[str, str]]:
    p = Path(path) if path else RULES_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    pairs = [(normalize_text(k), slug) for slug, kws in (data or {}).items() for k in kws]
    return sorted(pairs, key=lambda x: len(x[0]), reverse=True)  # most specific keyword first


def guess_category(*texts: str | None) -> str | None:
    for text in texts:
        t = f" {normalize_text(text)} "
        if t.strip():
            for kw, slug in _rules():
                if f" {kw} " in t:
                    return slug
    return None
