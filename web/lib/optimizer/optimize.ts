/**
 * Smart Basket & Fuel Optimizer
 *
 * For a set S of branches the shopper visits:
 *   Basket(S)  = Σ_items min_{b∈S} lineCost_b(item, qty)          (each item from its cheapest branch in S)
 *   Route(S)   = shortest tour home → S → home                     (exact for ≤7 stops)
 *   Fuel(S)    = Route_km × L/100km ÷ 100 × price_per_litre
 *   Time(S)    = (Route_km ÷ speed + stops × minutes_per_stop) × value_of_time      (0 by default)
 *   Penalty(S) = Σ_missing ref_price(item) × qty × (1 + λ)           (a missing item is not free)
 *   J(S)       = Basket + Fuel + Time + Penalty                     ← minimised
 *
 * Scenarios:
 *   single : argmin J(S), |S| = 1, inside the radius
 *   split  : argmin J(S), 2 ≤ |S| ≤ maxStores, inside the radius, route ≤ single + maxExtraKm;
 *            "worth it" only if J(single) − J(split) ≥ minSplitGain
 *   deals  : like split but wider radius, one more stop, and promo bundles rounded up when the
 *            extra units are free
 * Net savings = (Σ average market price of the bought items + fuel to the nearest branch)
 *               − (Basket + Fuel)
 */
import { bestRoute, haversineKm, roadKm } from "./geo";
import { effectiveUnit, lineCost } from "./pricing";
import type {
  BasketItem,
  Branch,
  LatLng,
  MissingItem,
  Note,
  OptimizeInput,
  OptimizeOptions,
  OptimizeResult,
  Plan,
  PlanLine,
  PlanStop,
  ScenarioKind,
} from "./types";

export const DEFAULT_OPTIONS: OptimizeOptions = {
  maxStores: 3,
  maxCandidates: 12,
  radiusKm: 15,
  roadFactor: 1.35,
  returnHome: true,
  missingPenaltyFactor: 0.25,
  minSplitGainUsd: 1,
  maxExtraKmForSplit: 12,
  dealsExtraStores: 1,
  dealsRadiusFactor: 1.5,
  bumpForFreeItems: true,
  timeValueUsdPerHour: 0,
  avgSpeedKmh: 28,
  minutesPerStop: 0,
  maxSubsets: 25_000,
};

const r2 = (x: number) => Math.round(x * 100) / 100;

interface Ctx {
  origin: LatLng;
  items: BasketItem[];
  subs: NonNullable<OptimizeInput["substitutes"]>;
  fuel: OptimizeInput["fuel"];
  o: OptimizeOptions;
  dist: (a: LatLng, b: LatLng) => number;
  refUnit: Map<string, number>; // median effective unit price (missing-item penalty)
  avgUnit: Map<string, number>; // mean effective unit price (baseline)
  nearestKm: number;
}

interface Evaluation {
  set: Branch[];
  lines: Map<string, PlanLine[]>; // branchId -> lines
  missing: MissingItem[];
  basket: number;
  penalty: number;
  route: ReturnType<typeof bestRoute>;
  used: Branch[];
  fuel: number;
  liters: number;
  minutes: number;
  timeCost: number;
  objective: number;
  substitutions: number;
}

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function evaluate(set: Branch[], ctx: Ctx, bump: boolean): Evaluation {
  const lines = new Map<string, PlanLine[]>();
  const missing: MissingItem[] = [];
  let basket = 0;
  let penalty = 0;
  let substitutions = 0;

  for (const item of ctx.items) {
    let best: { b: Branch; line: PlanLine } | null = null;
    for (const b of set) {
      const offer = b.offers[item.productId];
      if (!offer) continue;
      const c = lineCost(offer, item.qty, bump);
      if (!best || c.costUsd < best.line.costUsd - 1e-9) {
        best = {
          b,
          line: {
            productId: item.productId, name: item.name, qty: c.qtyBought, requestedQty: item.qty,
            unitPriceUsd: c.costUsd / c.qtyBought, costUsd: c.costUsd, regularCostUsd: c.regularCostUsd,
            promo: c.promo, extraFreeUnits: c.extraFreeUnits || undefined,
          },
        };
      }
    }
    if (!best && item.allowSubstitutes !== false) {
      for (const sub of ctx.subs[item.productId] ?? []) {
        const raw = item.qty * sub.qtyFactor;
        const q = Number.isInteger(item.qty) ? Math.max(1, Math.ceil(raw - 1e-9)) : r2(raw);
        for (const b of set) {
          const offer = b.offers[sub.productId];
          if (!offer) continue;
          const c = lineCost(offer, q, bump);
          if (!best || c.costUsd < best.line.costUsd - 1e-9) {
            best = {
              b,
              line: {
                productId: sub.productId, name: sub.name, qty: c.qtyBought, requestedQty: q,
                unitPriceUsd: c.costUsd / c.qtyBought, costUsd: c.costUsd, regularCostUsd: c.regularCostUsd,
                promo: c.promo, extraFreeUnits: c.extraFreeUnits || undefined,
                substituteFor: { productId: item.productId, name: item.name },
              },
            };
          }
        }
      }
    }
    if (best) {
      if (best.line.substituteFor) substitutions++;
      basket += best.line.costUsd;
      const arr = lines.get(best.b.branchId) ?? [];
      arr.push(best.line);
      lines.set(best.b.branchId, arr);
    } else {
      missing.push({ productId: item.productId, name: item.name, qty: item.qty });
      penalty += (ctx.refUnit.get(item.productId) ?? 0) * item.qty * (1 + ctx.o.missingPenaltyFactor);
    }
  }

  const used = set.filter((b) => lines.has(b.branchId));
  const route = bestRoute(ctx.origin, used, ctx.dist, ctx.o.returnHome);
  const liters = (route.totalKm * ctx.fuel.consumptionLPer100km) / 100;
  const fuel = liters * ctx.fuel.pricePerLiterUsd;
  const minutes = (route.totalKm / ctx.o.avgSpeedKmh) * 60 + used.length * ctx.o.minutesPerStop;
  const timeCost = (minutes / 60) * ctx.o.timeValueUsdPerHour;
  return {
    set, lines, missing, basket, penalty, route, used, fuel, liters, minutes, timeCost,
    objective: basket + fuel + timeCost + penalty, substitutions,
  };
}

function* combinations<T>(xs: T[], k: number, start = 0, acc: T[] = []): Generator<T[]> {
  if (acc.length === k) {
    yield acc.slice();
    return;
  }
  for (let i = start; i <= xs.length - (k - acc.length); i++) {
    acc.push(xs[i]);
    yield* combinations(xs, k, i + 1, acc);
    acc.pop();
  }
}

function nCk(n: number, k: number): number {
  if (k > n) return 0;
  let r = 1;
  for (let i = 1; i <= k; i++) r = (r * (n - k + i)) / i;
  return Math.round(r);
}

interface SearchResult {
  best: Evaluation | null;
  evaluated: number;
  method: "exact" | "greedy";
}

/** Best set with minSize..maxSize branches. Exact enumeration when affordable, else greedy add + swap. */
function search(
  cands: Branch[], minSize: number, maxSize: number, ctx: Ctx, bump: boolean,
  accept: (e: Evaluation) => boolean,
): SearchResult {
  let total = 0;
  for (let k = minSize; k <= maxSize; k++) total += nCk(cands.length, k);
  let best: Evaluation | null = null;
  let evaluated = 0;
  const consider = (e: Evaluation) => {
    evaluated++;
    if (e.used.length < minSize || !accept(e)) return;
    if (!best || e.objective < best.objective - 1e-9 ||
        (Math.abs(e.objective - best.objective) < 1e-9 && e.route.totalKm < best.route.totalKm)) best = e;
  };
  if (total <= ctx.o.maxSubsets) {
    for (let k = minSize; k <= maxSize; k++) for (const s of combinations(cands, k)) consider(evaluate(s, ctx, bump));
    return { best, evaluated, method: "exact" };
  }
  // greedy: grow from the best single branch, then try 1-swaps
  let cur: Branch[] = [];
  for (let k = 1; k <= maxSize; k++) {
    let stepBest: Evaluation | null = null;
    for (const c of cands) {
      if (cur.includes(c)) continue;
      const e = evaluate([...cur, c], ctx, bump);
      evaluated++;
      if (!stepBest || e.objective < stepBest.objective) stepBest = e;
    }
    if (!stepBest) break;
    cur = stepBest.set;
    if (k >= minSize) consider(stepBest);
  }
  let improved = true;
  while (improved && best) {
    improved = false;
    const b0: Evaluation = best;
    for (let i = 0; i < b0.set.length; i++) {
      for (const c of cands) {
        if (b0.set.includes(c)) continue;
        const s = [...b0.set];
        s[i] = c;
        const e = evaluate(s, ctx, bump);
        const prev: Evaluation = best;
        consider(e);
        if (best !== prev) improved = true;
      }
    }
  }
  return { best, evaluated, method: "greedy" };
}

function toPlan(kind: ScenarioKind, e: Evaluation, ctx: Ctx): Plan {
  const stops: PlanStop[] = e.route.order.map((i, n) => {
    const b = e.used[i];
    const lines = e.lines.get(b.branchId) ?? [];
    return {
      order: n + 1, branchId: b.branchId, storeId: b.storeId, storeName: b.storeName, branchName: b.branchName,
      lat: b.lat, lng: b.lng, legKm: r2(e.route.legsKm[n]), lines,
      subtotalUsd: r2(lines.reduce((s, l) => s + l.costUsd, 0)),
    };
  });
  const allLines = stops.flatMap((s) => s.lines);
  const dealSavings = allLines.reduce((s, l) => s + Math.max(0, l.regularCostUsd - l.costUsd), 0);

  // Baseline: average market price of the same items (original product for substitutions)
  // + a round trip to the nearest branch.
  const baselineBasket = allLines.reduce((s, l) => {
    const pid = l.substituteFor?.productId ?? l.productId;
    const qty = l.substituteFor ? ctx.items.find((i) => i.productId === pid)!.qty : l.requestedQty;
    const avg = ctx.avgUnit.get(pid) ?? ctx.avgUnit.get(l.productId) ?? l.unitPriceUsd;
    return s + avg * qty;
  }, 0);
  const baselineFuel =
    ((ctx.nearestKm * (ctx.o.returnHome ? 2 : 1) * ctx.fuel.consumptionLPer100km) / 100) * ctx.fuel.pricePerLiterUsd;
  const baseline = baselineBasket + baselineFuel;
  const total = e.basket + e.fuel;

  const notes: Note[] = [];
  if (e.missing.length) notes.push({ code: "missing_items", params: { count: e.missing.length } });
  if (e.substitutions) notes.push({ code: "substitutions", params: { count: e.substitutions } });
  const free = allLines.reduce((s, l) => s + (l.extraFreeUnits ?? 0), 0);
  if (free) notes.push({ code: "free_items_added", params: { count: free } });
  const promos = allLines.filter((l) => l.promo).length;
  if (promos) notes.push({ code: "promos_used", params: { count: promos, savedUsd: r2(dealSavings) } });

  return {
    kind, stops, returnLegKm: r2(e.route.returnKm), basketCostUsd: r2(e.basket), distanceKm: r2(e.route.totalKm),
    fuelLiters: r2(e.liters), fuelCostUsd: r2(e.fuel), driveMinutes: Math.round(e.minutes), timeCostUsd: r2(e.timeCost),
    totalCostUsd: r2(total), objectiveUsd: r2(e.objective), missing: e.missing, substitutions: e.substitutions,
    dealSavingsUsd: r2(dealSavings), baselineCostUsd: r2(baseline), netSavingsUsd: r2(baseline - total),
    netSavingsPct: baseline > 0 ? Math.round(((baseline - total) / baseline) * 1000) / 10 : 0,
    savingsVsSingleUsd: null, worthIt: true, notes,
  };
}

const sameSet = (a: Plan, b: Plan) =>
  a.stops.length === b.stops.length && a.stops.every((s) => b.stops.some((t) => t.branchId === s.branchId));

/** Keep the most promising branches: best single-store objective + the cheapest branch for each item. */
function prune(pool: Branch[], ctx: Ctx, limit: number): Branch[] {
  const ranked = pool
    .map((b) => ({ b, j: evaluate([b], ctx, false).objective }))
    .sort((x, y) => x.j - y.j)
    .map((x) => x.b);
  const keep = new Set(ranked.slice(0, limit));
  for (const item of ctx.items) {
    let cheapest: Branch | null = null;
    let price = Infinity;
    for (const b of pool) {
      const o = b.offers[item.productId];
      if (o && effectiveUnit(o, item.qty) < price) [cheapest, price] = [b, effectiveUnit(o, item.qty)];
    }
    if (cheapest && keep.size < limit + 4) keep.add(cheapest);
  }
  return ranked.filter((b) => keep.has(b));
}

export function optimizeBasket(input: OptimizeInput): OptimizeResult {
  const t0 = Date.now();
  const o: OptimizeOptions = { ...DEFAULT_OPTIONS, ...input.options };
  const dist = o.distanceKm ?? ((a: LatLng, b: LatLng) => roadKm(a, b, o.roadFactor));
  const items = input.items.filter((i) => i.qty > 0);
  const ids = new Set(items.map((i) => i.productId));
  const subIds = new Set(Object.values(input.substitutes ?? {}).flat().map((s) => s.productId));
  const branches = input.branches.filter((b) =>
    Object.keys(b.offers).some((p) => ids.has(p) || subIds.has(p)));

  // market statistics per product, over every loaded branch
  const refUnit = new Map<string, number>();
  const avgUnit = new Map<string, number>();
  for (const pid of new Set([...ids, ...subIds])) {
    const prices = branches.flatMap((b) => (b.offers[pid] ? [effectiveUnit(b.offers[pid])] : []));
    if (prices.length) {
      refUnit.set(pid, median(prices));
      avgUnit.set(pid, prices.reduce((s, x) => s + x, 0) / prices.length);
    }
  }
  const available = items.filter((i) =>
    refUnit.has(i.productId) ||
    (i.allowSubstitutes !== false && (input.substitutes?.[i.productId] ?? []).some((s) => refUnit.has(s.productId))));
  const unavailable = items.filter((i) => !available.includes(i))
    .map((i) => ({ productId: i.productId, name: i.name, qty: i.qty }));

  const straight = (b: Branch) => haversineKm(input.origin, b);
  const nearestKm = branches.length ? Math.min(...branches.map((b) => dist(input.origin, b))) : 0;
  const ctx: Ctx = {
    origin: input.origin, items: available, subs: input.substitutes ?? {}, fuel: input.fuel, o, dist,
    refUnit, avgUnit, nearestKm,
  };
  const empty: OptimizeResult = {
    single: null, split: null, deals: null, recommended: null, unavailable,
    meta: { branchesConsidered: 0, subsetsEvaluated: 0, ms: 0, method: "exact" },
  };
  if (!available.length || !branches.length) return { ...empty, meta: { ...empty.meta, ms: Date.now() - t0 } };

  let inRadius = branches.filter((b) => straight(b) <= o.radiusKm);
  const radiusNote = inRadius.length === 0;
  if (radiusNote) inRadius = [...branches].sort((a, b) => straight(a) - straight(b)).slice(0, 3);
  const dealsPool = branches.filter((b) => straight(b) <= o.radiusKm * o.dealsRadiusFactor);

  let evaluated = 0;
  let method: "exact" | "greedy" = "exact";

  // 1) single store
  const s1 = search(inRadius, 1, 1, ctx, false, () => true);
  evaluated += s1.evaluated;
  const single = s1.best ? toPlan("single", s1.best, ctx) : null;
  if (single && radiusNote) single.notes.unshift({ code: "no_branch_in_radius", params: { radiusKm: o.radiusKm } });

  // 2) smart split
  let split: Plan | null = null;
  if (single && o.maxStores >= 2) {
    const cands = prune(inRadius, ctx, o.maxCandidates);
    const maxKm = single.distanceKm + o.maxExtraKmForSplit;
    const s2 = search(cands, 2, Math.min(o.maxStores, cands.length), ctx, false, (e) => e.route.totalKm <= maxKm);
    evaluated += s2.evaluated;
    if (s2.method === "greedy") method = "greedy";
    if (s2.best) {
      split = toPlan("split", s2.best, ctx);
      const gain = single.objectiveUsd - split.objectiveUsd;
      split.savingsVsSingleUsd = r2(single.totalCostUsd - split.totalCostUsd);
      if (gain < o.minSplitGainUsd) {
        split.worthIt = false;
        split.notes.unshift({ code: "split_not_worth", params: { gainUsd: r2(gain), minUsd: o.minSplitGainUsd } });
      }
    }
  }

  // 3) max deals
  let deals: Plan | null = null;
  if (single && dealsPool.length) {
    const cands = prune(dealsPool, ctx, o.maxCandidates + 4);
    const maxK = Math.min(o.maxStores + o.dealsExtraStores, cands.length, 5);
    const s3 = search(cands, 1, maxK, ctx, o.bumpForFreeItems, () => true);
    evaluated += s3.evaluated;
    if (s3.method === "greedy") method = "greedy";
    if (s3.best) {
      deals = toPlan("deals", s3.best, ctx);
      deals.savingsVsSingleUsd = r2(single.totalCostUsd - deals.totalCostUsd);
      const ref = split?.worthIt ? split : single;
      const gain = ref.objectiveUsd - deals.objectiveUsd;
      if (gain < o.minSplitGainUsd) {
        deals.worthIt = false;
        if (sameSet(deals, ref)) deals.notes.unshift({ code: "same_as_other", params: { other: ref.kind } });
      }
    }
  }

  const plans = [single, split, deals].filter((p): p is Plan => !!p && p.worthIt);
  const recommended = plans.sort((a, b) => a.objectiveUsd - b.objectiveUsd || a.stops.length - b.stops.length)[0]?.kind ?? null;

  return {
    single, split, deals, recommended, unavailable,
    meta: { branchesConsidered: branches.length, subsetsEvaluated: evaluated, ms: Date.now() - t0, method },
  };
}
