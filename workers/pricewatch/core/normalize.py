"""RawListing -> NormalizedListing (USD price, parsed quantity, unit price, normalized names)."""
from __future__ import annotations

from decimal import Decimal

from .currency import DEFAULT_USD_LBP, PriceParseError, parse_price, to_usd
from .gtin import normalize_gtin
from .models import NormalizedListing, ProductIdentity, RawListing
from .text import brand_spellings, canonical_tokens, load_brand_aliases, normalize_brand, normalize_text, strip_tokens
from .units import parse_quantity, strip_quantities, unit_price

# Sanity bounds for a single supermarket item, in USD.
MIN_PRICE_USD = Decimal("0.05")
MAX_PRICE_USD = Decimal("2500")


class NormalizationError(ValueError):
    pass


def make_core_name(name_norm: str, brand_norm: str) -> str:
    """Name without brand and size tokens; identical rule for listings and canonical products."""
    core = strip_tokens(strip_quantities(name_norm), brand_spellings(brand_norm) if brand_norm else [])
    return canonical_tokens(core)


class Normalizer:
    def __init__(self, usd_lbp: Decimal = DEFAULT_USD_LBP, brand_aliases: dict[str, str] | None = None):
        self.usd_lbp = usd_lbp
        self.aliases = brand_aliases if brand_aliases is not None else load_brand_aliases()

    def normalize_listing(self, raw: RawListing) -> NormalizedListing:
        warnings: list[str] = []
        try:
            price, currency = parse_price(raw.price, raw.currency)
        except PriceParseError as e:
            raise NormalizationError(f"price: {e}") from e
        price_usd = to_usd(price, currency, self.usd_lbp)
        if not (MIN_PRICE_USD <= price_usd <= MAX_PRICE_USD):
            raise NormalizationError(f"price out of bounds: {price_usd} USD")

        regular_usd = None
        if raw.regular_price not in (None, ""):
            try:
                rp, rc = parse_price(raw.regular_price, currency)
                regular_usd = to_usd(rp, rc, self.usd_lbp)
                if regular_usd <= price_usd:
                    regular_usd = None  # not a real discount
            except PriceParseError:
                warnings.append("regular_price_unparsed")

        quantity = parse_quantity(raw.size_text, raw.name)
        if quantity is None:
            warnings.append("quantity_unparsed")

        brand_norm = normalize_brand(raw.brand, raw.name, self.aliases)
        name_norm = normalize_text(raw.name)
        name_core = make_core_name(name_norm, brand_norm)

        up, basis = (unit_price(price_usd, quantity) if quantity else (None, None))
        return NormalizedListing(
            raw=raw,
            name_norm=name_norm,
            name_core=name_core,
            brand_norm=brand_norm,
            quantity=quantity,
            price=price,
            currency=currency,
            price_usd=price_usd,
            regular_price_usd=regular_usd,
            unit_price_usd=up,
            unit_price_basis=basis,  # type: ignore[arg-type]
            gtin=normalize_gtin(raw.gtin),
            warnings=warnings,
        )

    def identity(self, name: str, brand: str | None = None, size_text: str | None = None,
                 gtin: str | None = None) -> ProductIdentity:
        brand_norm = normalize_brand(brand, name, self.aliases)
        name_norm = normalize_text(name)
        return ProductIdentity(name_norm, make_core_name(name_norm, brand_norm), brand_norm,
                               parse_quantity(size_text, name), normalize_gtin(gtin))
