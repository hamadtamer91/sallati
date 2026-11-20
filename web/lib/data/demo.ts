/* Demo data source: fictional chains, realistic-ish Lebanese prices. Used when Supabase env vars are empty. */
import type { Branch, LatLng, Offer, Promo, Substitute } from "../optimizer";
import { haversineKm } from "../optimizer";
import type { DataSource, Deal, FlyerRow, MarketSettings, Named, ProductSummary, RunRow } from "./types";

interface P { id: string; en: string; ar: string; brand: string | null; size: string; cat: string; usd: number }
const PRODUCTS: P[] = [
  { id: "nido-900", en: "Nido Fortified Milk Powder", ar: "حليب نيدو بودرة", brand: "Nido", size: "900 g", cat: "milk", usd: 9.6 },
  { id: "almarai-1l", en: "Almarai Full Fat Milk", ar: "حليب المراعي كامل الدسم", brand: "Almarai", size: "1 L", cat: "milk", usd: 2.1 },
  { id: "almarai-2l", en: "Almarai Full Fat Milk", ar: "حليب المراعي كامل الدسم", brand: "Almarai", size: "2 L", cat: "milk", usd: 3.9 },
  { id: "labneh-500", en: "Taanayel Labneh", ar: "لبنة تعنايل", brand: "Taanayel", size: "500 g", cat: "labneh-yogurt", usd: 3.3 },
  { id: "puck-500", en: "Puck Cream Cheese Spread", ar: "جبنة بوك قابلة للدهن", brand: "Puck", size: "500 g", cat: "cheese", usd: 5.2 },
  { id: "eggs-30", en: "Fresh Eggs (large)", ar: "بيض طازج كبير", brand: null, size: "30 pcs", cat: "dairy", usd: 5.4 },
  { id: "rice-2kg", en: "Uncle Ben's Long Grain Rice", ar: "أرز أنكل بنز حبة طويلة", brand: "Uncle Ben's", size: "2 kg", cat: "rice-grains", usd: 6.0 },
  { id: "lentils-900", en: "Al Wadi Al Akhdar Red Lentils", ar: "عدس أحمر الوادي الأخضر", brand: "Al Wadi Al Akhdar", size: "900 g", cat: "rice-grains", usd: 2.4 },
  { id: "afia-18", en: "Afia Corn Oil", ar: "زيت ذرة عافية", brand: "Afia", size: "1.8 L", cat: "oil", usd: 6.9 },
  { id: "olive-1l", en: "Lebanese Extra Virgin Olive Oil", ar: "زيت زيتون لبناني بكر ممتاز", brand: null, size: "1 L", cat: "oil", usd: 9.5 },
  { id: "sugar-2kg", en: "White Sugar", ar: "سكر أبيض", brand: null, size: "2 kg", cat: "pantry", usd: 2.2 },
  { id: "lipton-100", en: "Lipton Yellow Label Tea", ar: "شاي ليبتون", brand: "Lipton", size: "100 bags", cat: "pantry", usd: 4.5 },
  { id: "nescafe-200", en: "Nescafé Classic", ar: "نسكافيه كلاسيك", brand: "Nescafé", size: "200 g", cat: "pantry", usd: 8.9 },
  { id: "sohat-6", en: "Sohat Natural Water", ar: "مياه صحة", brand: "Sohat", size: "6 × 1.5 L", cat: "water", usd: 2.4 },
  { id: "tannourine-6", en: "Tannourine Natural Water", ar: "مياه تنورين", brand: "Tannourine", size: "6 × 1.5 L", cat: "water", usd: 2.3 },
  { id: "coke-6", en: "Coca-Cola", ar: "كوكا كولا", brand: "Coca-Cola", size: "6 × 330 ml", cat: "soft-drinks", usd: 3.2 },
  { id: "pepsi-225", en: "Pepsi", ar: "بيبسي", brand: "Pepsi", size: "2.25 L", cat: "soft-drinks", usd: 1.9 },
  { id: "ariel-3kg", en: "Ariel Automatic Powder", ar: "مسحوق أريال أوتوماتيك", brand: "Ariel", size: "3 kg", cat: "detergents", usd: 11.5 },
  { id: "fine-12", en: "Fine Toilet Tissue", ar: "محارم تواليت فاين", brand: "Fine", size: "12 rolls", cat: "tissues", usd: 5.8 },
  { id: "pampers-4", en: "Pampers Baby-Dry Size 4", ar: "حفاضات بامبرز مقاس 4", brand: "Pampers", size: "44 pcs", cat: "baby", usd: 16.5 },
  { id: "tomato-kg", en: "Tomatoes", ar: "بندورة", brand: null, size: "1 kg", cat: "produce", usd: 1.1 },
  { id: "cucumber-kg", en: "Cucumbers", ar: "خيار", brand: null, size: "1 kg", cat: "produce", usd: 0.9 },
  { id: "chicken-kg", en: "Chicken Breast", ar: "صدر دجاج", brand: null, size: "1 kg", cat: "meat", usd: 7.5 },
  { id: "bread-l", en: "Arabic Bread (large)", ar: "خبز عربي كبير", brand: null, size: "1 pack", cat: "bakery", usd: 1.0 },
];

interface Chain { id: string; en: string; ar: string; mult: number; coverage: number; branches: { id: string; en: string; ar: string; lat: number; lng: number; region: string }[] }
const CHAINS: Chain[] = [
  { id: "cedar", en: "Cedar Mart", ar: "سيدر مارت", mult: 1.0, coverage: 0.95, branches: [
    { id: "cedar-ash", en: "Achrafieh", ar: "الأشرفية", lat: 33.8869, lng: 35.521, region: "beirut" },
    { id: "cedar-jdd", en: "Jdeideh", ar: "الجديدة", lat: 33.8936, lng: 35.5647, region: "metn" },
    { id: "cedar-jnh", en: "Jounieh", ar: "جونية", lat: 33.9808, lng: 35.6178, region: "kesrouan" }] },
  { id: "bayt", en: "Bayt Market", ar: "بيت ماركت", mult: 0.95, coverage: 0.9, branches: [
    { id: "bayt-dek", en: "Dekwaneh", ar: "الدكوانة", lat: 33.879, lng: 35.545, region: "metn" },
    { id: "bayt-hmr", en: "Hamra", ar: "الحمرا", lat: 33.8963, lng: 35.4829, region: "beirut" }] },
  { id: "port", en: "Port Fresh", ar: "بورت فريش", mult: 1.06, coverage: 0.92, branches: [
    { id: "port-mmk", en: "Mar Mikhael", ar: "مار مخايل", lat: 33.8975, lng: 35.5265, region: "beirut" },
    { id: "port-dby", en: "Dbayeh", ar: "ضبية", lat: 33.945, lng: 35.587, region: "metn" }] },
  { id: "souk", en: "Souk Saver", ar: "سوق سيفر", mult: 0.88, coverage: 0.65, branches: [
    { id: "souk-sef", en: "Sin El Fil", ar: "سن الفيل", lat: 33.871, lng: 35.533, region: "metn" },
    { id: "souk-zlk", en: "Zalka", ar: "الزلقا", lat: 33.908, lng: 35.575, region: "metn" }] },
];

const plusDays = (n: number) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);

// chain-level promotions (valid -3 .. +10 days)
const PROMOS: Record<string, Record<string, Promo & { regular?: number }>> = {
  port: {
    "nido-900": { kind: "price_cut", promoPriceUsd: 8.49 },
    "coke-6": { kind: "multibuy", buyQty: 2, promoPriceUsd: 5.5 },
    "ariel-3kg": { kind: "percent_off", percentOff: 20 },
    "olive-1l": { kind: "price_cut", promoPriceUsd: 8.25 },
  },
  cedar: {
    "almarai-1l": { kind: "bogo", buyQty: 2, getQty: 1 },
    "rice-2kg": { kind: "price_cut", promoPriceUsd: 5.25 },
    "pampers-4": { kind: "percent_off", percentOff: 15 },
  },
  bayt: {
    "pepsi-225": { kind: "multibuy", buyQty: 3, promoPriceUsd: 4.75 },
    "eggs-30": { kind: "price_cut", promoPriceUsd: 4.6 },
  },
  souk: { "fine-12": { kind: "bogo", buyQty: 1, getQty: 1 } },
};

function hash01(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10000) / 10000;
}
const round05 = (x: number) => Math.round(x * 20) / 20;

function offerFor(chain: Chain, branchId: string, p: P): Offer | null {
  if (hash01(`${branchId}:${p.id}:avail`) > chain.coverage) return null;
  const chainNoise = 1 + (hash01(`${chain.id}:${p.id}`) - 0.5) * 0.16;
  const branchNoise = 1 + (hash01(`${branchId}:${p.id}`) - 0.5) * 0.04;
  const price = Math.max(0.25, round05(p.usd * chain.mult * chainNoise * branchNoise));
  const promo = PROMOS[chain.id]?.[p.id];
  return {
    priceUsd: price,
    regularPriceUsd: promo?.kind === "price_cut" ? price : null,
    promo: promo ? { ...promo, id: `${chain.id}-${p.id}`, validUntil: plusDays(10) } : null,
  };
}

const norm = (s: string) =>
  s.toLowerCase()
    .replace(/[أإآٱ]/g, "ا").replace(/ى/g, "ي").replace(/ة/g, "ه")
    .replace(/[ً-ْـ]/g, "").replace(/[^\p{L}\p{N}.]+/gu, " ").trim();

const AR_UNITS: [RegExp, string][] = [[/\bkg\b/, "كغ"], [/\bg\b/, "غ"], [/\bml\b/, "مل"], [/\bL\b/, "ل"], [/\bpcs\b/, "حبة"], [/\brolls\b/, "رول"], [/\bbags\b/, "كيس"], [/\bpack\b/, "ربطة"]];
const arSize = (s: string) => AR_UNITS.reduce((acc, [re, ar]) => acc.replace(re, ar), s);

function summary(p: P): ProductSummary {
  const prices: number[] = [];
  for (const c of CHAINS) for (const b of c.branches) {
    const o = offerFor(c, b.id, p);
    if (o) prices.push(o.priceUsd);
  }
  return {
    id: p.id, nameEn: `${p.en} ${p.size}`, nameAr: `${p.ar} ${arSize(p.size)}`, brand: p.brand, sizeLabel: p.size,
    category: p.cat, minPriceUsd: prices.length ? Math.min(...prices) : null, offersCount: prices.length, imageUrl: null,
  };
}

const REGIONS: Named[] = [
  { slug: "beirut", nameEn: "Beirut", nameAr: "بيروت" },
  { slug: "metn", nameEn: "Metn", nameAr: "المتن" },
  { slug: "kesrouan", nameEn: "Kesrouan", nameAr: "كسروان" },
];
const CATEGORIES: Named[] = [
  ["milk", "Milk", "حليب"], ["cheese", "Cheese", "أجبان"], ["labneh-yogurt", "Labneh & Yogurt", "لبنة ولبن"],
  ["dairy", "Dairy & Eggs", "ألبان وبيض"], ["rice-grains", "Rice & Grains", "أرز وحبوب"], ["oil", "Cooking Oil", "زيوت"],
  ["pantry", "Pantry", "مؤونة"], ["water", "Water", "مياه"], ["soft-drinks", "Soft Drinks", "مشروبات غازية"],
  ["detergents", "Detergents", "منظفات"], ["tissues", "Tissues", "محارم"], ["baby", "Baby", "أطفال"],
  ["produce", "Fruits & Vegetables", "خضار وفواكه"], ["meat", "Meat & Poultry", "لحوم ودواجن"], ["bakery", "Bakery", "مخبوزات"],
].map(([slug, nameEn, nameAr]) => ({ slug, nameEn, nameAr }));

export const demoSource: DataSource = {
  mode: "demo",

  async searchProducts(q, limit = 20) {
    const nq = norm(q);
    const hits = PRODUCTS.filter((p) => !nq || norm(`${p.en} ${p.ar} ${p.brand ?? ""} ${p.cat}`).includes(nq));
    return hits.slice(0, limit).map(summary);
  },

  async branchesForBasket(productIds: string[], origin: LatLng, radiusKm: number): Promise<Branch[]> {
    const out: Branch[] = [];
    for (const c of CHAINS) for (const b of c.branches) {
      if (haversineKm(origin, b) > radiusKm) continue;
      const offers: Record<string, Offer> = {};
      for (const pid of productIds) {
        const p = PRODUCTS.find((x) => x.id === pid);
        const o = p && offerFor(c, b.id, p);
        if (o) offers[pid] = o;
      }
      if (Object.keys(offers).length) {
        out.push({ branchId: b.id, storeId: c.id, storeName: c.en, branchName: `${c.ar} — ${b.ar}`, lat: b.lat, lng: b.lng, offers });
      }
    }
    return out;
  },

  async substitutes(productIds) {
    const pairs: Record<string, Substitute[]> = {
      "sohat-6": [{ productId: "tannourine-6", name: "Tannourine Natural Water 6 × 1.5 L", qtyFactor: 1 }],
      "tannourine-6": [{ productId: "sohat-6", name: "Sohat Natural Water 6 × 1.5 L", qtyFactor: 1 }],
      "almarai-1l": [{ productId: "almarai-2l", name: "Almarai Full Fat Milk 2 L", qtyFactor: 0.5 }],
    };
    return Object.fromEntries(productIds.filter((id) => pairs[id]).map((id) => [id, pairs[id]]));
  },

  async market(): Promise<MarketSettings> {
    return { usdLbpRate: 89700, fuel95Per20lLbp: 2844000, fuel98Per20lLbp: 2862000, fuelEffectiveDate: "2026-09-29" };
  },

  async regions() { return REGIONS; },
  async categories() { return CATEGORIES; },

  async deals(filter) {
    const deals: Deal[] = [];
    for (const c of CHAINS) {
      if (filter.region && !c.branches.some((b) => b.region === filter.region)) continue;
      for (const [pid, promo] of Object.entries(PROMOS[c.id] ?? {})) {
        const p = PRODUCTS.find((x) => x.id === pid)!;
        if (filter.category && p.cat !== filter.category) continue;
        const o = offerFor(c, c.branches[0].id, p) ?? { priceUsd: round05(p.usd * c.mult) };
        const unit = promo.kind === "price_cut" ? promo.promoPriceUsd!
          : promo.kind === "percent_off" ? o.priceUsd * (1 - promo.percentOff! / 100)
          : promo.kind === "multibuy" ? promo.promoPriceUsd! / promo.buyQty!
          : (o.priceUsd * promo.buyQty!) / (promo.buyQty! + promo.getQty!);
        deals.push({
          id: `${c.id}-${pid}`, storeNameEn: c.en, storeNameAr: c.ar, title: `${p.en} ${p.size}`,
          nameEn: `${p.en} ${p.size}`, nameAr: `${p.ar} ${arSize(p.size)}`, brand: p.brand, sizeText: p.size, category: p.cat,
          promoType: promo.kind, promoPriceUsd: promo.promoPriceUsd ?? null, regularPriceUsd: o.priceUsd,
          percentOff: promo.percentOff ?? null, buyQty: promo.buyQty ?? null, getQty: promo.getQty ?? null,
          discountPct: Math.round((1 - unit / o.priceUsd) * 1000) / 10,
          validFrom: plusDays(-3), validUntil: plusDays(pid.length % 2 ? 4 : 10),
          source: c.id === "port" || c.id === "bayt" ? "flyer" : "site",
        });
      }
    }
    return filter.sort === "ending"
      ? deals.sort((a, b) => (a.validUntil ?? "9999").localeCompare(b.validUntil ?? "9999"))
      : deals.sort((a, b) => (b.discountPct ?? 0) - (a.discountPct ?? 0));
  },

  async runs(): Promise<RunRow[]> {
    const t = (h: number) => new Date(Date.now() - h * 36e5).toISOString();
    const row = (id: number, store: string, adapter: string, kind: RunRow["kind"], status: RunRow["status"], h: number,
      seen: number, failed = 0, baseline: number | null = seen, alerts: RunRow["alerts"] = [], fresh = 0): RunRow => ({
      id, store, adapter, kind, status, startedAt: t(h), finishedAt: t(h - 0.1), itemsSeen: seen,
      itemsUpserted: seen - failed, itemsFailed: failed, newProducts: fresh, baseline, alerts,
    });
    return [
      row(108, "Cedar Mart", "shopify", "catalog", "success", 1, 4210, 12, 4190, [], 7),
      row(107, "Bayt Market", "json_api", "catalog", "partial", 1.2, 1300, 4, 3020, [
        { code: "item_count_drop", severity: "critical", message: "Only 1300 products vs usual 3020 (-57%)" }]),
      row(106, "Port Fresh", "html_css", "catalog", "failed", 1.4, 880, 402, 2100, [
        { code: "schema_drift", severity: "critical", message: "46% of products failed to parse (price×402) — source format may have changed" }]),
      row(105, "Souk Saver", "woocommerce_store_api", "catalog", "success", 1.5, 1650, 3, 1622, [], 2),
      row(104, "Port Fresh", "flyer_links", "flyer", "success", 7, 64, 0, null, [
        { code: "flyer_review", severity: "warning", message: "Some flyer offers need manual review" }]),
      row(103, "Cedar Mart", "shopify", "catalog", "success", 7, 4188, 10, 4180),
    ];
  },

  async flyers(): Promise<FlyerRow[]> {
    const t = (h: number) => new Date(Date.now() - h * 36e5).toISOString();
    return [
      { id: "f3", store: "Port Fresh", title: "weekly-offers-w47.pdf", status: "needs_review", pages: 8, extracted: 64,
        accepted: 51, llmCalls: 49, validFrom: plusDays(-2), validUntil: plusDays(5), createdAt: t(7), sourceUrl: null, error: null },
      { id: "f2", store: "Bayt Market", title: "offers-p1.jpg", status: "done", pages: 1, extracted: 18, accepted: 18,
        llmCalls: 7, validFrom: plusDays(-3), validUntil: plusDays(10), createdAt: t(30), sourceUrl: null, error: null },
      { id: "f1", store: "Cedar Mart", title: "catalogue-nov.pdf", status: "failed", pages: null, extracted: 0, accepted: 0,
        llmCalls: 0, validFrom: null, validUntil: null, createdAt: t(55), sourceUrl: null, error: "PDF is password protected" },
    ];
  },
};

