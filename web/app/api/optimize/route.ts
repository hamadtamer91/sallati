import { NextResponse } from "next/server";
import { getDataSource } from "@/lib/data";
import { fuelPerLiterUsd } from "@/lib/money";
import { DEFAULT_OPTIONS, optimizeBasket, type BasketItem } from "@/lib/optimizer";

interface Body {
  items: BasketItem[];
  origin: { lat: number; lng: number };
  fuel: { pricePer20L: number; currency: "USD" | "LBP"; consumptionLPer100km: number };
  options?: { maxStores?: number; radiusKm?: number; roadFactor?: number; timeValueUsdPerHour?: number };
}

const clamp = (v: unknown, lo: number, hi: number, dflt: number) => {
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : dflt;
};

function parse(b: any): Body | string {
  if (!b || !Array.isArray(b.items) || b.items.length === 0) return "items_required";
  if (b.items.length > 100) return "too_many_items";
  const { lat, lng } = b.origin ?? {};
  if (!(lat >= 33 && lat <= 34.8 && lng >= 35 && lng <= 36.7)) return "origin_outside_lebanon";
  const items: BasketItem[] = b.items.map((i: any) => ({
    productId: String(i.productId), name: String(i.name ?? "").slice(0, 120),
    qty: clamp(i.qty, 0.1, 99, 1), allowSubstitutes: i.allowSubstitutes !== false,
  }));
  const currency = b.fuel?.currency === "USD" ? "USD" : "LBP";
  return {
    items, origin: { lat, lng },
    fuel: {
      currency,
      // 0 / empty = use the latest official price
      pricePer20L: Number(b.fuel?.pricePer20L) > 0
        ? clamp(b.fuel.pricePer20L, currency === "USD" ? 1 : 50_000, currency === "USD" ? 200 : 20_000_000, 0)
        : 0,
      consumptionLPer100km: clamp(b.fuel?.consumptionLPer100km, 2, 40, 9),
    },
    options: {
      maxStores: clamp(b.options?.maxStores, 1, 4, 3),
      radiusKm: clamp(b.options?.radiusKm, 1, 60, 15),
      roadFactor: clamp(b.options?.roadFactor, 1, 2.5, DEFAULT_OPTIONS.roadFactor),
      timeValueUsdPerHour: clamp(b.options?.timeValueUsdPerHour, 0, 100, 0),
    },
  };
}

export async function POST(req: Request) {
  const body = parse(await req.json().catch(() => null));
  if (typeof body === "string") return NextResponse.json({ error: body }, { status: 400 });
  try {
    const ds = getDataSource();
    const stale = body.items.map((i) => i.productId).filter((id) => !ds.isProductId(id));
    if (stale.length) return NextResponse.json({ error: "basket_outdated", stale }, { status: 409 });
    const market = await ds.market();
    const rate = market.usdLbpRate;
    const pricePer20L = body.fuel.pricePer20L || market.fuel95Per20lLbp || 2_800_000;
    const currency = body.fuel.pricePer20L ? body.fuel.currency : "LBP";
    const ids = body.items.map((i) => i.productId);
    const subs = await ds.substitutes(ids);
    const allIds = [...new Set([...ids, ...Object.values(subs).flat().map((s) => s.productId)])];
    const radius = body.options!.radiusKm!;
    const branches = await ds.branchesForBasket(allIds, body.origin, radius * DEFAULT_OPTIONS.dealsRadiusFactor);
    const result = optimizeBasket({
      origin: body.origin, items: body.items, branches, substitutes: subs,
      fuel: { pricePerLiterUsd: fuelPerLiterUsd(pricePer20L, currency, rate), consumptionLPer100km: body.fuel.consumptionLPer100km },
      options: body.options,
    });
    return NextResponse.json({ result, rate, fuelPerLiterUsd: fuelPerLiterUsd(pricePer20L, currency, rate), mode: ds.mode });
  } catch (e) {
    console.error(e);
    return NextResponse.json({ error: "optimize_failed" }, { status: 500 });
  }
}
