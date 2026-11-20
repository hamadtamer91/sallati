import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import type { Branch, LatLng, Offer, PromoKind, Substitute } from "../optimizer";
import type { DataSource, Deal, FlyerRow, Named, ProductSummary, RunRow } from "./types";
import { sizeLabel } from "./types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const num = (v: unknown): number | null => (v === null || v === undefined ? null : Number(v));

function publicClient(): SupabaseClient {
  return createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!, {
    auth: { persistSession: false },
  });
}

/** Service-role client: server only, used for the scraper monitor (bypasses RLS). */
function adminClient(): SupabaseClient {
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!key) throw new Error("SUPABASE_SERVICE_ROLE_KEY is not set");
  return createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, key, { auth: { persistSession: false } });
}

function check<T>(res: { data: T | null; error: { message: string } | null }): T {
  if (res.error) throw new Error(res.error.message);
  return res.data as T;
}

export function supabaseSource(): DataSource {
  const db = publicClient();
  return {
    mode: "supabase",

    async searchProducts(q, limit = 20): Promise<ProductSummary[]> {
      const rows = check(await db.rpc("search_products", { p_query: q, p_limit: limit })) as any[];
      return rows.map((r) => ({
        id: r.id, nameEn: r.name_en, nameAr: r.name_ar, brand: r.brand,
        sizeLabel: sizeLabel(r.base_unit, Number(r.unit_size), r.pack_count), category: r.category_slug,
        minPriceUsd: num(r.min_price_usd), offersCount: Number(r.offers_count), imageUrl: r.image_url,
      }));
    },

    isProductId: (id: string) => UUID.test(id),

    async branchesForBasket(productIds: string[], origin: LatLng, radiusKm: number): Promise<Branch[]> {
      productIds = productIds.filter((id) => UUID.test(id));
      if (!productIds.length) return [];
      const rows = check(await db.rpc("offers_for_basket", {
        p_product_ids: productIds, p_lat: origin.lat, p_lng: origin.lng, p_radius_km: radiusKm,
      })) as any[];
      const byBranch = new Map<string, Branch>();
      for (const r of rows) {
        let b = byBranch.get(r.branch_id);
        if (!b) {
          b = {
            branchId: r.branch_id, storeId: r.store_id, storeName: r.store_name_en,
            branchName: `${r.store_name_ar} — ${r.branch_name_ar ?? r.branch_name_en}`, lat: r.lat, lng: r.lng, offers: {},
          };
          byBranch.set(r.branch_id, b);
        }
        const offer: Offer = {
          priceUsd: Number(r.price_usd),
          regularPriceUsd: num(r.regular_price_usd),
          promo: r.promo_type ? {
            id: r.promo_id, kind: r.promo_type as PromoKind, promoPriceUsd: num(r.promo_price_usd),
            percentOff: num(r.percent_off), buyQty: r.buy_qty, getQty: r.get_qty, validUntil: r.promo_valid_until,
          } : null,
        };
        b.offers[r.canonical_product_id] = offer;
      }
      return [...byBranch.values()];
    },

    async substitutes(productIds) {
      productIds = productIds.filter((id) => UUID.test(id));
      if (!productIds.length) return {};
      const rows = check(await db.rpc("product_substitutes", { p_product_ids: productIds })) as any[];
      const out: Record<string, Substitute[]> = {};
      for (const r of rows) {
        (out[r.product_id] ??= []).push({
          productId: r.substitute_id, name: r.name_ar ?? r.name_en, qtyFactor: Number(r.qty_factor), similarity: r.similarity,
        });
      }
      return out;
    },

    async market() {
      const r = (check(await db.rpc("market_settings")) as any[])[0] ?? {};
      return {
        usdLbpRate: Number(r.usd_lbp_rate ?? 89700), fuel95Per20lLbp: num(r.gasoline_95_per_20l_lbp),
        fuel98Per20lLbp: num(r.gasoline_98_per_20l_lbp), fuelEffectiveDate: r.fuel_effective_date ?? null,
      };
    },

    async regions(): Promise<Named[]> {
      const rows = check(await db.from("regions").select("slug,name_en,name_ar").order("name_en")) as any[];
      return rows.map((r) => ({ slug: r.slug, nameEn: r.name_en, nameAr: r.name_ar }));
    },

    async categories(): Promise<Named[]> {
      const rows = check(await db.from("categories").select("slug,name_en,name_ar").order("sort_order")) as any[];
      return rows.map((r) => ({ slug: r.slug, nameEn: r.name_en, nameAr: r.name_ar }));
    },

    async deals(f): Promise<Deal[]> {
      const rows = check(await db.rpc("explore_deals", {
        p_region_slug: f.region || null, p_category_slug: f.category || null, p_sort: f.sort ?? "discount", p_limit: 90,
      })) as any[];
      return rows.map((r) => ({
        id: r.promo_id, storeNameEn: r.store_name_en, storeNameAr: r.store_name_ar, title: r.title,
        nameEn: r.name_en, nameAr: r.name_ar, brand: r.brand, sizeText: r.size_text, category: r.category_slug,
        promoType: r.promo_type, promoPriceUsd: num(r.promo_price_usd), regularPriceUsd: num(r.regular_price_usd),
        percentOff: num(r.percent_off), buyQty: r.buy_qty, getQty: r.get_qty, discountPct: num(r.discount_pct),
        validFrom: r.valid_from, validUntil: r.valid_until, source: r.source,
      }));
    },

    async runs(limit = 50): Promise<RunRow[]> {
      const rows = check(await adminClient().from("scraper_runs")
        .select("*, stores(name_en)").order("started_at", { ascending: false }).limit(limit)) as any[];
      return rows.map((r) => ({
        id: r.id, store: r.stores?.name_en ?? "—", adapter: r.adapter_key, kind: r.run_kind, status: r.status,
        startedAt: r.started_at, finishedAt: r.finished_at, itemsSeen: r.items_seen, itemsUpserted: r.items_upserted,
        itemsFailed: r.items_failed, newProducts: r.new_products, baseline: r.baseline_items, alerts: r.alerts ?? [],
      }));
    },

    async flyers(limit = 30): Promise<FlyerRow[]> {
      const rows = check(await adminClient().from("flyers")
        .select("*, stores(name_en)").order("created_at", { ascending: false }).limit(limit)) as any[];
      return rows.map((r) => ({
        id: r.id, store: r.stores?.name_en ?? "—", title: r.title, status: r.status, pages: r.page_count,
        extracted: r.items_extracted, accepted: r.items_accepted, llmCalls: r.llm_calls, validFrom: r.valid_from,
        validUntil: r.valid_until, createdAt: r.created_at, sourceUrl: r.source_url, error: r.error,
      }));
    },
  };
}
