from decimal import Decimal

import pytest

from pricewatch.core.currency import parse_price, to_usd
from pricewatch.core.gtin import normalize_gtin
from pricewatch.core.matching import ProductMatcher
from pricewatch.core.models import BaseUnit, Currency, RawListing
from pricewatch.core.normalize import Normalizer
from pricewatch.core.text import normalize_brand, normalize_text
from pricewatch.core.units import parse_quantity, same_quantity, unit_price
from pricewatch.storage.memory import MemoryRepository

G, ML, PC = BaseUnit.G, BaseUnit.ML, BaseUnit.PIECE


@pytest.mark.parametrize("text,size,unit,pack,by_weight", [
    ("500g", "500", G, 1, False),
    ("حليب نيدو بودرة ٩٠٠ غ", "900", G, 1, False),
    ("1.5L", "1500", ML, 1, False),
    ("1,5 ل", "1500", ML, 1, False),
    ("Coca-Cola Original 6x330ml", "330", ML, 6, False),
    ("كوكا كولا ٦ × ٣٣٠ مل", "330", ML, 6, False),
    ("330ml x 24", "330", ML, 24, False),
    ("مياه صحة 1.5 ل - 6 حبات", "1500", ML, 6, False),
    ("Water 1.5L pack of 6", "1500", ML, 6, False),
    ("2 kg", "2000", G, 1, False),
    ("أرز ٢ كغ", "2000", G, 1, False),
    ("بيض طازج كرتونة 30 حبة", "30", PC, 1, False),
    ("30 pcs", "30", PC, 1, False),
    ("Tomatoes (per kg)", "1000", G, 1, True),
    ("بندورة بالكيلو", "1000", G, 1, True),
    ("Labneh 1,000 g", "1000", G, 1, False),
    ("لبنة تعنايل 500 غ", "500", G, 1, False),
])
def test_parse_quantity(text, size, unit, pack, by_weight):
    q = parse_quantity(text)
    assert q is not None, text
    assert (q.unit_size, q.base_unit, q.pack_count, q.sold_by_weight) == (Decimal(size), unit, pack, by_weight)


def test_quantity_does_not_eat_lira_or_words():
    assert parse_quantity("845,000 ل.ل") is None
    assert parse_quantity("لبن بلدي") is None


def test_same_quantity_rules():
    one_l, thousand_ml = parse_quantity("1L"), parse_quantity("1000 ml")
    assert same_quantity(one_l, thousand_ml)
    assert not same_quantity(parse_quantity("330ml"), parse_quantity("6x330ml"))  # piece vs pack
    assert not same_quantity(parse_quantity("1L"), parse_quantity("1.5L"))


def test_unit_price():
    assert unit_price(Decimal("3.20"), parse_quantity("6x330ml")) == (Decimal("1.616162"), "l")
    assert unit_price(Decimal("5.50"), parse_quantity("30 pcs"))[1] == "piece"


@pytest.mark.parametrize("raw,hint,amount,cur", [
    ("$9.75", None, "9.75", Currency.USD),
    ("845,000 ل.ل", None, "845000", Currency.LBP),
    ("275.000 ل.ل", None, "275000", Currency.LBP),
    ("520,000 LBP", None, "520000", Currency.LBP),
    ("190000", None, "190000", Currency.LBP),     # magnitude rule
    ("2.40", None, "2.40", Currency.USD),
    ("١٢٫٥٠ دولار", None, "12.50", Currency.USD),
    ("1,99", Currency.USD, "1.99", Currency.USD),
    ("1.234,50 $", None, "1234.50", Currency.USD),
    ("Kitchen roll 1.99", None, "1.99", Currency.USD),  # "roll" must not look like "LL"
    ("1.5$ للكيلو", None, "1.5", Currency.USD),        # "لل" must not look like ل.ل
])
def test_parse_price(raw, hint, amount, cur):
    assert parse_price(raw, hint) == (Decimal(amount), cur)


def test_to_usd():
    assert to_usd(Decimal("897000"), Currency.LBP, Decimal("89700")) == Decimal("10.0000")


def test_gtin():
    assert normalize_gtin("7613030000907") == "07613030000907"
    assert normalize_gtin("7613030000908") is None   # bad check digit
    assert normalize_gtin("٧٦١٣٠٣٠٠٠٠٩٠٧") == "07613030000907"
    assert normalize_gtin("12345") is None


def test_text_and_brand():
    assert normalize_text("حليبٌ  نيدو، ١.٥ لتر — أإآ ة ى") == "حليب نيدو 1.5 لتر ااا ه ي"
    assert normalize_brand("كوكا كولا") == "coca cola"
    assert normalize_brand(None, "حليب المراعي كامل الدسم") == "almarai"


def _n(name, price="1", **kw):
    return Normalizer().normalize_listing(RawListing(external_id="x", name=name, price=price, currency="USD", **kw))


def test_matching_end_to_end():
    repo = MemoryRepository()
    m = ProductMatcher(repo.catalog_index())

    a = _n("Almarai Full Fat Milk 1L", brand="Almarai")
    r = m.match(a)
    assert r.method == "new" and r.create_new
    cid = repo.create_canonical(a, a.raw.name, "Almarai", None, None)

    b = _n("حليب المراعي كامل الدسم 1000 مل")  # Arabic, brand detected from name, 1000ml == 1L
    rb = m.match(b)
    assert rb.canonical_id == cid and rb.method == "attributes"   # Arabic synonyms map to the English name

    choc = _n("Almarai Chocolate Milk 1L", brand="Almarai")
    assert m.match(choc).canonical_id is None or m.match(choc).needs_review   # never silently merged

    c = _n("Almarai Full Fat Milk 1L - fresh", brand="almarai")
    assert m.match(c).canonical_id == cid

    six = _n("Almarai Full Fat Milk 6x1L", brand="Almarai")
    assert m.match(six).canonical_id is None       # pack of 6 never matches the single bottle

    noqty = _n("Almarai milk", brand="Almarai")
    rn = m.match(noqty)
    assert rn.canonical_id is None and rn.needs_review


def test_matching_by_barcode_wins():
    repo = MemoryRepository()
    m = ProductMatcher(repo.catalog_index())
    a = _n("Nido Fortified Milk Powder 900g", brand="Nido", gtin="7613030000907")
    cid = repo.create_canonical(a, a.raw.name, "Nido", None, None)
    b = _n("حليب نيدو بودرة ٩٠٠ غ", gtin="7613030000907")
    r = m.match(b)
    assert (r.canonical_id, r.method, r.confidence) == (cid, "barcode", 1.0)


def test_similar_names_are_not_merged():
    repo = MemoryRepository()
    m = ProductMatcher(repo.catalog_index())
    a = _n("Toast Original 200g", brand="Lighter Line")
    repo.create_canonical(a, a.raw.name, "Lighter Line", None, None)
    r = m.match(_n("Toast Whole Grain 200g", brand="Lighter Line"))
    assert r.canonical_id is None and r.create_new and r.review_reason == "possible_duplicate"
