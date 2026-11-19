import type { Offer, PromoKind } from "./types";

export interface LineCost {
  qtyBought: number;
  costUsd: number;
  regularCostUsd: number; // what the requested quantity costs at the regular ("was") price
  promo?: PromoKind;
  extraFreeUnits: number;
}

const EPS = 1e-9;

/** Cost of buying `qty` units of an offer, applying its promotion.
 *
 *  price_cut    : qty × min(shelf, promo)
 *  percent_off  : qty × shelf × (1 − p/100)
 *  multibuy N/X : ⌊qty/N⌋ × X + (qty mod N) × shelf          ("3 for $5")
 *  bogo N+M     : (qty − ⌊qty/(N+M)⌋ × M) × shelf             ("buy 2 get 1")
 *
 *  With `bump`, quantity is rounded up to the next full bundle only when that costs
 *  the same or less (i.e. the extra units are free) — never spends more money. */
export function lineCost(offer: Offer, qty: number, bump = false): LineCost {
  const shelf = offer.priceUsd;
  const regularUnit = Math.max(offer.regularPriceUsd ?? shelf, shelf);
  const regularCostUsd = qty * regularUnit;
  const plain: LineCost = { qtyBought: qty, costUsd: qty * shelf, regularCostUsd, extraFreeUnits: 0 };
  const p = offer.promo;
  if (!p) return plain;

  const costFor = (q: number): number => {
    switch (p.kind) {
      case "price_cut":
        return q * Math.min(shelf, p.promoPriceUsd ?? shelf);
      case "percent_off":
        return q * shelf * (1 - Math.min(Math.max(p.percentOff ?? 0, 0), 95) / 100);
      case "multibuy": {
        const n = p.buyQty ?? 0;
        const x = p.promoPriceUsd ?? Infinity;
        if (n < 2 || x >= n * shelf) return q * shelf;
        const bundles = Math.floor(q / n + EPS);
        return bundles * x + (q - bundles * n) * shelf;
      }
      case "bogo": {
        const n = p.buyQty ?? 1;
        const m = p.getQty ?? 1;
        const free = Math.floor(q / (n + m) + EPS) * m;
        return (q - free) * shelf;
      }
    }
  };

  let best: LineCost = { ...plain, costUsd: costFor(qty), promo: undefined };
  if (best.costUsd < plain.costUsd - EPS) best.promo = p.kind;
  else best = plain;

  if (bump && Number.isInteger(qty) && (p.kind === "multibuy" || p.kind === "bogo")) {
    const group = p.kind === "multibuy" ? (p.buyQty ?? 0) : (p.buyQty ?? 1) + (p.getQty ?? 1);
    if (group >= 2 && qty % group !== 0) {
      const q2 = Math.ceil(qty / group) * group;
      const c2 = costFor(q2);
      if (c2 <= best.costUsd + EPS) {
        best = { qtyBought: q2, costUsd: c2, regularCostUsd, promo: p.kind, extraFreeUnits: q2 - qty };
      }
    }
  }
  return best;
}

/** Effective unit price used for ranking (no bump). */
export function effectiveUnit(offer: Offer, qty = 1): number {
  return lineCost(offer, qty).costUsd / qty;
}
