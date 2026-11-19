import { describe, expect, it } from "vitest";
import { bestRoute, haversineKm, lineCost, optimizeBasket, type Branch, type OptimizeInput } from "./index";

const HOME = { lat: 33.888, lng: 35.52 };
// ~1 km of latitude ≈ 0.009°
const at = (kmNorth: number, kmEast = 0) => ({ lat: HOME.lat + kmNorth * 0.008993, lng: HOME.lng + kmEast * 0.01081 });

function branch(id: string, pos: { lat: number; lng: number }, prices: Record<string, number>, extra: Partial<Branch> = {}): Branch {
  return {
    branchId: id, storeId: id, storeName: id, branchName: id, ...pos,
    offers: Object.fromEntries(Object.entries(prices).map(([p, v]) => [p, { priceUsd: v }])), ...extra,
  };
}

const FUEL = { pricePerLiterUsd: 1.5, consumptionLPer100km: 10 }; // $0.15 per km
const base = (branches: Branch[], items = [{ productId: "milk", name: "Milk", qty: 2 }, { productId: "rice", name: "Rice", qty: 1 }]): OptimizeInput => ({
  origin: HOME, items, branches, fuel: FUEL, options: { roadFactor: 1 },
});

describe("geometry", () => {
  it("haversine is about right for Beirut → Jounieh", () => {
    const d = haversineKm({ lat: 33.8938, lng: 35.5018 }, { lat: 33.9808, lng: 35.6178 });
    expect(d).toBeGreaterThan(14);
    expect(d).toBeLessThan(15);
  });

  it("route is the shortest closed tour", () => {
    const stops = [at(4), at(1), at(2)];
    const r = bestRoute(HOME, stops, haversineKm);
    expect([[1, 2, 0], [0, 2, 1]]).toContainEqual(r.order); // same tour, either direction
    expect(r.totalKm).toBeCloseTo(8, 0);
  });
});

describe("line cost with promotions", () => {
  it("multibuy 3 for $5 and free-unit bump", () => {
    const offer = { priceUsd: 2.6, promo: { kind: "multibuy" as const, buyQty: 3, promoPriceUsd: 5 } };
    expect(lineCost(offer, 3).costUsd).toBeCloseTo(5);
    expect(lineCost(offer, 4).costUsd).toBeCloseTo(7.6);
    const bumped = lineCost(offer, 2, true); // 2 × 2.6 = 5.2 > 5 for 3 → buy 3
    expect(bumped.qtyBought).toBe(3);
    expect(bumped.extraFreeUnits).toBe(1);
    expect(bumped.costUsd).toBeCloseTo(5);
  });

  it("buy 2 get 1 free", () => {
    const offer = { priceUsd: 1, promo: { kind: "bogo" as const, buyQty: 2, getQty: 1 } };
    expect(lineCost(offer, 3).costUsd).toBe(2);
    expect(lineCost(offer, 2).costUsd).toBe(2);
    expect(lineCost(offer, 2, true)).toMatchObject({ qtyBought: 3, costUsd: 2, extraFreeUnits: 1 });
  });

  it("never applies a promo that is more expensive than the shelf price", () => {
    const offer = { priceUsd: 1, promo: { kind: "price_cut" as const, promoPriceUsd: 1.2 } };
    const c = lineCost(offer, 2);
    expect(c.costUsd).toBe(2);
    expect(c.promo).toBeUndefined();
  });
});

describe("optimizer", () => {
  it("fuel is computed as km × L/100km × price", () => {
    const r = optimizeBasket(base([branch("A", at(5), { milk: 1, rice: 2 })]));
    expect(r.single!.distanceKm).toBeCloseTo(10, 0);
    expect(r.single!.fuelLiters).toBeCloseTo(1, 1);
    expect(r.single!.fuelCostUsd).toBeCloseTo(1.5, 1);
    expect(r.single!.basketCostUsd).toBe(4);
  });

  it("single store: a slightly cheaper but far store loses after fuel", () => {
    const near = branch("near", at(1), { milk: 1.1, rice: 2 });
    const far = branch("far", at(10), { milk: 1, rice: 2 }); // saves $0.2, costs ~$2.7 more fuel
    const r = optimizeBasket(base([near, far]));
    expect(r.single!.stops[0].branchId).toBe("near");
    expect(r.recommended).toBe("single");
  });

  it("smart split only when the price gap beats the extra fuel", () => {
    const a = branch("A", at(1), { milk: 3, rice: 2 });
    const b = branch("B", at(1, 1), { milk: 1, rice: 6 });
    const r = optimizeBasket(base([a, b]));
    expect(r.split!.worthIt).toBe(true);
    expect(r.split!.basketCostUsd).toBe(4); // milk 2×1 from B, rice 2 from A
    expect(r.recommended).not.toBe("single");

    const c = branch("C", at(1, 1), { milk: 2.9, rice: 6 }); // saves only $0.2 on milk
    const r2 = optimizeBasket(base([a, c]));
    expect(r2.split?.worthIt ?? false).toBe(false);
    expect(r2.recommended).toBe("single");
  });

  it("a store missing an item is penalised, and missing items are reported", () => {
    const cheapIncomplete = branch("X", at(1), { milk: 0.9 });
    const complete = branch("Y", at(1.2), { milk: 1, rice: 2 });
    const r = optimizeBasket(base([cheapIncomplete, complete]));
    expect(r.single!.stops[0].branchId).toBe("Y");
    // rice exists only far away: the near single-store plan reports it as missing
    const farRice = branch("Z", at(14), { rice: 2 });
    const r2 = optimizeBasket({ ...base([cheapIncomplete, farRice]), options: { roadFactor: 1, maxStores: 1 } });
    expect(r2.single!.missing.map((m) => m.productId)).toEqual(["rice"]);
    // rice exists nowhere: excluded from every plan and listed as unavailable
    const rOnlyX = optimizeBasket(base([cheapIncomplete]));
    expect(rOnlyX.single!.missing).toEqual([]);
    expect(rOnlyX.unavailable.map((m) => m.productId)).toEqual(["rice"]);
  });

  it("uses a substitute when the product is not in the chosen stores", () => {
    const s = branch("S", at(1), { milk: 1, rice500: 1.2 });
    const input: OptimizeInput = {
      ...base([s]),
      substitutes: { rice: [{ productId: "rice500", name: "Rice 500g", qtyFactor: 2 }] },
    };
    const r = optimizeBasket(input);
    const line = r.single!.stops[0].lines.find((l) => l.substituteFor)!;
    expect(line).toMatchObject({ productId: "rice500", qty: 2, substituteFor: { productId: "rice" } });
    expect(r.single!.missing).toHaveLength(0);
  });

  it("max deals adds free units and reports deal savings", () => {
    const d = branch("D", at(1), { milk: 1, rice: 2 }, {});
    d.offers.milk = { priceUsd: 1, regularPriceUsd: 1.2, promo: { kind: "bogo", buyQty: 1, getQty: 1 } };
    const r = optimizeBasket(base([d], [{ productId: "milk", name: "Milk", qty: 1 }, { productId: "rice", name: "Rice", qty: 1 }]));
    const line = r.deals!.stops[0].lines.find((l) => l.productId === "milk")!;
    expect(line.extraFreeUnits).toBe(1);
    expect(r.deals!.dealSavingsUsd).toBeCloseTo(0.2);
  });

  it("net savings = average-market basket + fuel to nearest − plan cost", () => {
    const a = branch("A", at(1), { milk: 1, rice: 2 });
    const b = branch("B", at(1.5), { milk: 2, rice: 3 });
    const r = optimizeBasket(base([a, b]));
    // avg milk 1.5×2 + avg rice 2.5 = 5.5 ; plan basket 4 ; same fuel (A is nearest)
    expect(r.single!.baselineCostUsd - r.single!.fuelCostUsd).toBeCloseTo(5.5, 1);
    expect(r.single!.netSavingsUsd).toBeCloseTo(1.5, 1);
  });

  it("handles many branches quickly", () => {
    const many: Branch[] = [];
    for (let i = 0; i < 40; i++) {
      many.push(branch(`b${i}`, at((i % 7) - 3, Math.floor(i / 7) - 3), {
        milk: 1 + ((i * 37) % 10) / 10, rice: 2 + ((i * 53) % 10) / 10, oil: 4 + ((i * 17) % 10) / 10,
      }));
    }
    const r = optimizeBasket(base(many, [
      { productId: "milk", name: "Milk", qty: 2 }, { productId: "rice", name: "Rice", qty: 1 },
      { productId: "oil", name: "Oil", qty: 1 },
    ]));
    expect(r.single).not.toBeNull();
    expect(r.meta.ms).toBeLessThan(2000);
  });
});
