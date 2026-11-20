import type { Branch, LatLng, PromoKind, Substitute } from "../optimizer";

export interface ProductSummary {
  id: string;
  nameEn: string | null;
  nameAr: string | null;
  brand: string | null;
  sizeLabel: string;
  category: string | null;
  minPriceUsd: number | null;
  offersCount: number;
  imageUrl: string | null;
}

export interface MarketSettings {
  usdLbpRate: number;
  fuel95Per20lLbp: number | null;
  fuel98Per20lLbp: number | null;
  fuelEffectiveDate: string | null;
}

export interface Named {
  slug: string;
  nameEn: string;
  nameAr: string;
}

export interface Deal {
  id: string;
  storeNameEn: string;
  storeNameAr: string;
  title: string;
  nameEn: string | null;
  nameAr: string | null;
  brand: string | null;
  sizeText: string | null;
  category: string | null;
  promoType: PromoKind;
  promoPriceUsd: number | null;
  regularPriceUsd: number | null;
  percentOff: number | null;
  buyQty: number | null;
  getQty: number | null;
  discountPct: number | null;
  validFrom: string;
  validUntil: string | null; // null = discount shown on the store's site, no end date
  source: "flyer" | "site";
}

export interface RunRow {
  id: number;
  store: string;
  adapter: string;
  kind: "catalog" | "flyer";
  status: "running" | "success" | "partial" | "failed";
  startedAt: string;
  finishedAt: string | null;
  itemsSeen: number;
  itemsUpserted: number;
  itemsFailed: number;
  newProducts: number;
  baseline: number | null;
  alerts: { code: string; severity: "warning" | "critical"; message: string }[];
}

export interface FlyerRow {
  id: string;
  store: string;
  title: string | null;
  status: "queued" | "processing" | "done" | "needs_review" | "failed";
  pages: number | null;
  extracted: number;
  accepted: number;
  llmCalls: number;
  validFrom: string | null;
  validUntil: string | null;
  createdAt: string;
  sourceUrl: string | null;
  error: string | null;
}

export interface DealFilter {
  region?: string;
  category?: string;
  sort?: "discount" | "ending";
}

export interface DataSource {
  mode: "demo" | "supabase";
  /** False for ids from another catalog (e.g. a basket saved while the site ran on demo data). */
  isProductId(id: string): boolean;
  searchProducts(q: string, limit?: number): Promise<ProductSummary[]>;
  branchesForBasket(productIds: string[], origin: LatLng, radiusKm: number): Promise<Branch[]>;
  substitutes(productIds: string[]): Promise<Record<string, Substitute[]>>;
  market(): Promise<MarketSettings>;
  regions(): Promise<Named[]>;
  categories(): Promise<Named[]>;
  deals(filter: DealFilter): Promise<Deal[]>;
  runs(limit?: number): Promise<RunRow[]>;
  flyers(limit?: number): Promise<FlyerRow[]>;
}

export function sizeLabel(baseUnit: string, unitSize: number, pack: number): string {
  const one =
    baseUnit === "g" ? (unitSize >= 1000 ? `${unitSize / 1000} kg` : `${unitSize} g`)
    : baseUnit === "ml" ? (unitSize >= 1000 ? `${unitSize / 1000} L` : `${unitSize} ml`)
    : `${unitSize} pcs`;
  return pack > 1 ? `${pack} × ${one}` : one;
}
