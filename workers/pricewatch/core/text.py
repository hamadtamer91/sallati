"""Arabic/English text normalization. Mirrors SQL norm_text() in the migration."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

_FOLD = str.maketrans(
    "أإآٱىةؤئ٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹×",
    "اااايهوي01234567890123456789x",
)
_DIGITS_ONLY = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_TASHKEEL = re.compile(r"[ً-ْٰـ]")
_NON_WORD = re.compile(r"(?:[^\w.]|_)+")
_SPACES = re.compile(r"\s+")


def to_ascii_digits(s: str) -> str:
    """Arabic-Indic / Persian digits and separators -> ASCII."""
    return s.translate(_DIGITS_ONLY)


def normalize_text(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).lower().translate(_FOLD)
    s = _TASHKEEL.sub("", s)
    s = _NON_WORD.sub(" ", s)
    return _SPACES.sub(" ", s).strip(" .")


DEFAULT_ALIASES_PATH = Path(__file__).resolve().parents[2] / "config" / "brand_aliases.yaml"


@lru_cache(maxsize=4)
def load_brand_aliases(path: str | None = None) -> dict[str, str]:
    """Map every known spelling (normalized) -> canonical brand key."""
    p = Path(path) if path else DEFAULT_ALIASES_PATH
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for canonical, spellings in data.items():
        key = normalize_text(canonical)
        out[key] = key
        for sp in spellings or []:
            out[normalize_text(sp)] = key
    return out


def normalize_brand(brand: str | None, name: str = "", aliases: dict[str, str] | None = None) -> str:
    """Return the canonical brand key; detect it inside the product name if missing."""
    aliases = aliases if aliases is not None else load_brand_aliases()
    b = normalize_text(brand)
    if b:
        return aliases.get(b, b)
    n = f" {normalize_text(name)} "
    # longest alias first so "al wadi al akhdar" wins over "wadi"
    for spelling in sorted(aliases, key=len, reverse=True):
        if f" {spelling} " in n:
            return aliases[spelling]
    return ""


SYNONYMS_PATH = Path(__file__).resolve().parents[2] / "config" / "name_synonyms.yaml"


@lru_cache(maxsize=2)
def load_name_synonyms(path: str | None = None) -> tuple[tuple[tuple[str, str], ...], frozenset[str]]:
    """(phrase -> english token) pairs, longest first, and the stopword set."""
    p = Path(path) if path else SYNONYMS_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    data = data or {}
    stop = frozenset(normalize_text(w) for w in data.pop("_stopwords", []) or [])
    pairs = [(normalize_text(v), normalize_text(k)) for k, vs in data.items() for v in vs or []]
    return tuple(sorted(pairs, key=lambda x: len(x[0]), reverse=True)), stop


def brand_spellings(brand_norm: str, aliases: dict[str, str] | None = None) -> list[str]:
    aliases = aliases if aliases is not None else load_brand_aliases()
    return [brand_norm] + [sp for sp, key in aliases.items() if key == brand_norm and sp != brand_norm]


def canonical_tokens(text: str) -> str:
    """Translate known Arabic words to English tokens and drop generic words (matching only)."""
    pairs, stop = load_name_synonyms()
    out = f" {text} "
    for phrase, token in pairs:
        out = out.replace(f" {phrase} ", f" {token} ")
    return " ".join(t for t in out.split() if t not in stop)


def strip_tokens(text: str, tokens: list[str]) -> str:
    out = f" {text} "
    for t in sorted({t for t in tokens if t}, key=len, reverse=True):
        out = out.replace(f" {t} ", " ")
    return _SPACES.sub(" ", out).strip()
