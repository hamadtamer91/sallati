export type LatLng = { lat: number; lng: number };

export type PromoKind = "price_cut" | "percent_off" | "multibuy" | "bogo";

export interface Promo {
  id?: string | null;
  kind: PromoKind;
  promoPriceUsd?: number | null; // price_cut: unit price; multibuy: price of the whole bundle
  percentOff?: number | null;
  buyQty?: number | null; // multibuy: N in "N for X"; bogo: N in "buy N get M"
  getQty?: number | null; // bogo: M
  validUntil?: string | null;
}

export interface Offer {
  priceUsd: number; // shelf price of one unit
  regularPriceUsd?: number | null; // "was" price, if the store shows one
  promo?: Promo | null;
}

export interface Branch {
  branchId: string;
  storeId: string;
  storeName: string;
  branchName: string;
  lat: number;
  lng: number;
  offers: Record<string, Offer>; // productId -> offer
}

export interface BasketItem {
  productId: string;
  name: string;
  qty: number;
  allowSubstitutes?: boolean;
}

export interface Substitute {
  productId: string;
  name: string;
  qtyFactor: number; // units of the substitute that replace 1 unit of the original
  similarity?: number;
}

export interface FuelParams {
  pricePerLiterUsd: number;
  consumptionLPer100km: number;
}

export interface OptimizeOptions {
  maxStores: number;
  maxCandidates: number;
  radiusKm: number;
  roadFactor: number;
  returnHome: boolean;
  missingPenaltyFactor: number;
  minSplitGainUsd: number;
  maxExtraKmForSplit: number;
  dealsExtraStores: number;
  dealsRadiusFactor: number;
  bumpForFreeItems: boolean;
  timeValueUsdPerHour: number;
  avgSpeedKmh: number;
  minutesPerStop: number;
  maxSubsets: number;
  distanceKm?: (a: LatLng, b: LatLng) => number;
}

export interface OptimizeInput {
  origin: LatLng;
  items: BasketItem[];
  branches: Branch[];
  substitutes?: Record<string, Substitute[]>;
  fuel: FuelParams;
  options?: Partial<OptimizeOptions>;
}

export type ScenarioKind = "single" | "split" | "deals";

export interface PlanLine {
  productId: string;
  name: string;
  qty: number; // units actually bought (may include free extra units)
  requestedQty: number;
  unitPriceUsd: number; // effective price per unit bought
  costUsd: number;
  regularCostUsd: number;
  promo?: PromoKind;
  extraFreeUnits?: number;
  substituteFor?: { productId: string; name: string };
}

export interface PlanStop {
  order: number;
  branchId: string;
  storeId: string;
  storeName: string;
  branchName: string;
  lat: number;
  lng: number;
  legKm: number;
  lines: PlanLine[];
  subtotalUsd: number;
}

export interface Note {
  code:
    | "no_branch_in_radius"
    | "split_not_worth"
    | "split_too_far"
    | "missing_items"
    | "substitutions"
    | "free_items_added"
    | "same_as_other"
    | "promos_used";
  params?: Record<string, string | number>;
}

export interface MissingItem {
  productId: string;
  name: string;
  qty: number;
}

export interface Plan {
  kind: ScenarioKind;
  stops: PlanStop[];
  returnLegKm: number;
  basketCostUsd: number;
  distanceKm: number;
  fuelLiters: number;
  fuelCostUsd: number;
  driveMinutes: number;
  timeCostUsd: number;
  totalCostUsd: number; // basket + fuel (money actually spent)
  objectiveUsd: number; // total + time value + missing-item penalty (what the optimizer minimises)
  missing: MissingItem[];
  substitutions: number;
  dealSavingsUsd: number;
  baselineCostUsd: number;
  netSavingsUsd: number;
  netSavingsPct: number;
  savingsVsSingleUsd: number | null;
  worthIt: boolean;
  notes: Note[];
}

export interface OptimizeResult {
  single: Plan | null;
  split: Plan | null;
  deals: Plan | null;
  recommended: ScenarioKind | null;
  unavailable: MissingItem[];
  meta: { branchesConsidered: number; subsetsEvaluated: number; ms: number; method: "exact" | "greedy" };
}
