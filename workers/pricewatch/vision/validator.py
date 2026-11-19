"""Business validation of offers extracted from flyers (dates, prices, promo shape)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from ..core.currency import PriceParseError, parse_price, to_usd
from ..core.models import Currency, RawPromotion
from ..core.text import to_ascii_digits

Status = Literal["active", "pending_review", "rejected", "expired"]


@dataclass
class ValidatedPromotion:
    raw: RawPromotion
    promo_type: str
    price: Decimal | None
    currency: Currency | None
    promo_price_usd: Decimal | None
    regular_price_usd: Decimal | None
    percent_off: Decimal | None
    buy_qty: int | None
    get_qty: int | None
    valid_from: date
    valid_until: date
    dates_inferred: bool
    status: Status
    confidence: float
    reasons: list[str] = field(default_factory=list)


_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DMY = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?$")


def parse_date(value: str | date | None, ref: date) -> date | None:
    """ISO, or Lebanese day/month[/year]. Missing year -> the year that puts it closest to ref."""
    if value is None or isinstance(value, date):
        return value
    s = to_ascii_digits(str(value)).strip()
    try:
        m = _ISO.match(s)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = _DMY.match(s)
        if m:
            d, mo = int(m[1]), int(m[2])
            if m[3]:
                y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
                return date(y, mo, d)
            options = [date(ref.year + k, mo, d) for k in (-1, 0, 1)]
            return min(options, key=lambda x: abs((x - ref).days))
    except ValueError:
        return None
    return None


class PromotionValidator:
    def __init__(self, today: date, usd_lbp: Decimal, min_confidence: float = 0.6,
                 max_window_days: int = 60, default_window_days: int = 7, max_discount_pct: float = 80):
        self.today = today
        self.usd_lbp = usd_lbp
        self.min_confidence = min_confidence
        self.max_window = max_window_days
        self.default_window = default_window_days
        self.max_discount = max_discount_pct

    def _usd(self, v, cur: Currency | None) -> tuple[Decimal | None, Currency | None, Decimal | None]:
        if v in (None, ""):
            return None, cur, None
        amount, c = parse_price(v, cur)
        return amount, c, to_usd(amount, c, self.usd_lbp)

    def validate(self, item: RawPromotion, flyer_from: date | None = None, flyer_until: date | None = None,
                 default_currency: Currency | None = None) -> ValidatedPromotion:
        reasons: list[str] = []
        status: Status = "active"
        cur = item.currency or default_currency

        try:
            price, cur, price_usd = self._usd(item.price, cur)
            _, _, regular_usd = self._usd(item.regular_price, cur)
        except PriceParseError:
            price = price_usd = regular_usd = None
            reasons.append("price_unparsed")

        promo_type = item.promo_type
        buy, get = item.buy_qty, item.get_qty
        pct = Decimal(str(item.percent_off)) if item.percent_off is not None else None

        if promo_type == "percent_off" and pct is None and price_usd and regular_usd:
            pct = (100 * (1 - price_usd / regular_usd)).quantize(Decimal("0.1"))
        if promo_type == "bogo":
            buy, get = buy or 1, get or 1
        if promo_type == "multibuy" and (not buy or buy < 2):
            promo_type = "price_cut"  # "N for X" without N is just a price
            reasons.append("multibuy_without_qty")

        if regular_usd is not None and price_usd is not None:
            unit_promo = price_usd / buy if (promo_type == "multibuy" and buy) else price_usd
            if regular_usd <= unit_promo:
                regular_usd = None
                reasons.append("regular_not_higher")
            elif 100 * (1 - unit_promo / regular_usd) > self.max_discount:
                reasons.append("discount_too_large")

        # shape checks (mirror the DB constraint promotions_shape_chk)
        hard_fail = (
            (promo_type in ("price_cut", "multibuy") and price_usd is None)
            or (promo_type == "percent_off" and (pct is None or not 0 < pct <= 95))
        )
        if price_usd is not None and not (Decimal("0.05") <= price_usd <= Decimal("2500")):
            hard_fail = True
            reasons.append("price_out_of_bounds")
        if pct is not None and pct > Decimal(str(self.max_discount)):
            reasons.append("discount_too_large")

        # dates: item -> flyer -> inferred
        vf = parse_date(item.valid_from, self.today) or flyer_from
        vu = parse_date(item.valid_until, self.today) or flyer_until
        inferred = False
        if vf is None and vu is None:
            vf, vu, inferred = self.today, self.today + timedelta(days=self.default_window), True
            reasons.append("dates_inferred")
        elif vf is None:
            vf = min(self.today, vu)  # type: ignore[type-var]
        elif vu is None:
            vu, inferred = vf + timedelta(days=self.default_window), True
            reasons.append("end_date_inferred")
        assert vf and vu
        if vu < vf:
            vf, vu = vu, vf
            reasons.append("dates_swapped")
        if (vu - vf).days > 120:
            hard_fail = True
            reasons.append("window_too_long")
        elif (vu - vf).days > self.max_window:
            reasons.append("window_long")
        if vf > self.today + timedelta(days=45):
            reasons.append("starts_far_future")
        if item.confidence < self.min_confidence:
            reasons.append("low_confidence")

        if hard_fail:
            status = "rejected"
        elif vu < self.today:
            status = "expired"
        elif any(r not in ("regular_not_higher",) for r in reasons):
            status = "pending_review"

        return ValidatedPromotion(
            raw=item, promo_type=promo_type, price=price, currency=cur,
            promo_price_usd=price_usd, regular_price_usd=regular_usd, percent_off=pct,
            buy_qty=buy if promo_type in ("multibuy", "bogo") else None,
            get_qty=get if promo_type == "bogo" else None,
            valid_from=vf, valid_until=vu, dates_inferred=inferred,
            status=status, confidence=item.confidence, reasons=reasons,
        )
