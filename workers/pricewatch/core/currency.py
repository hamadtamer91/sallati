"""Price parsing for Lebanese stores (USD and LBP, Arabic or English formatting)."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .models import Currency
from .text import to_ascii_digits

DEFAULT_USD_LBP = Decimal("89700")
# Anything at or above this with no currency marker is assumed to be LBP.
LBP_MAGNITUDE_THRESHOLD = Decimal("1000")

_LBP_MARK = re.compile(
    r"(?:\blbp\b|\bl\.\s?l\b|\bll\b|\blira\b|ليرة|ليره|(?<![^\W\d_])ل\.?\s?ل(?![^\W\d_]))", re.I
)
_USD_MARK = re.compile(r"(?:\$|usd|us\$|دولار|\bdollars?\b)", re.I)
_NUMBER = re.compile(r"\d[\d.,\s]*")


class PriceParseError(ValueError):
    pass


def _to_decimal(num: str, currency: Currency | None) -> Decimal:
    s = num.replace(" ", "").strip(".,")
    if not s:
        raise PriceParseError("empty number")
    dots, commas = s.count("."), s.count(",")
    if dots and commas:  # "1,234.50" or "1.234,50": the last separator is the decimal one
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        s = s.replace(thou, "").replace(dec, ".")
    elif commas:
        parts = s.split(",")
        # "178,000" thousands ; "1,99" decimal
        s = s.replace(",", "") if all(len(p) == 3 for p in parts[1:]) else s.replace(",", ".")
    elif dots > 1 or (dots == 1 and len(s.split(".")[1]) == 3 and currency is not Currency.USD):
        s = s.replace(".", "")  # "178.000" (LBP style thousands)
    try:
        return Decimal(s)
    except InvalidOperation as e:
        raise PriceParseError(f"bad number {num!r}") from e


def parse_price(value: Decimal | float | int | str | None, hint: Currency | None = None) -> tuple[Decimal, Currency]:
    if value is None or value == "":
        raise PriceParseError("missing price")
    if isinstance(value, (int, float, Decimal)):
        amount = Decimal(str(value))
        currency = hint
    else:
        text = to_ascii_digits(str(value))
        currency = hint
        if currency is None:
            if _LBP_MARK.search(text):
                currency = Currency.LBP
            elif _USD_MARK.search(text):
                currency = Currency.USD
        m = _NUMBER.search(text)
        if not m:
            raise PriceParseError(f"no number in {value!r}")
        amount = _to_decimal(m.group(0), currency)
    if amount <= 0:
        raise PriceParseError("price must be positive")
    if currency is None:
        currency = Currency.LBP if amount >= LBP_MAGNITUDE_THRESHOLD else Currency.USD
    return amount, currency


def to_usd(amount: Decimal, currency: Currency, usd_lbp: Decimal = DEFAULT_USD_LBP) -> Decimal:
    usd = amount if currency is Currency.USD else amount / usd_lbp
    return usd.quantize(Decimal("0.0001"))
