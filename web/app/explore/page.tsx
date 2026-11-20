import { getDataSource, type Deal } from "@/lib/data";
import { getDict, type Dict, type Lang } from "@/lib/i18n";
import { getLang } from "@/lib/lang";
import { lbp, usd } from "@/lib/money";

export const dynamic = "force-dynamic";

function promoLabel(d: Deal, t: Dict, lang: Lang): string {
  switch (d.promoType) {
    case "percent_off":
      return `-${d.percentOff}%`;
    case "multibuy":
      return lang === "ar" ? `${d.buyQty} بـ ${usd(d.promoPriceUsd ?? 0)}` : `${d.buyQty} for ${usd(d.promoPriceUsd ?? 0)}`;
    case "bogo":
      return `${d.buyQty}+${d.getQty} ${t.free}`;
    default:
      return d.discountPct ? `-${Math.round(d.discountPct)}%` : "";
  }
}

export default async function ExplorePage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const lang = await getLang();
  const t = getDict(lang);
  const ds = getDataSource();
  const filter = { region: sp.region || undefined, category: sp.category || undefined, sort: sp.sort === "ending" ? "ending" as const : "discount" as const };
  const [regions, categories, deals, market] = await Promise.all([ds.regions(), ds.categories(), ds.deals(filter), ds.market()]);
  const name = (x: { nameEn: string; nameAr: string }) => (lang === "ar" ? x.nameAr : x.nameEn);

  return (
    <>
      <h1 className="mb-4 text-2xl font-bold">{t.exploreTitle}</h1>
      <form className="card mb-5 grid gap-3 sm:grid-cols-4" method="get">
        <label>
          <span className="label">{t.region}</span>
          <select name="region" defaultValue={filter.region ?? ""} className="input">
            <option value="">{t.all}</option>
            {regions.map((r) => <option key={r.slug} value={r.slug}>{name(r)}</option>)}
          </select>
        </label>
        <label>
          <span className="label">{t.category}</span>
          <select name="category" defaultValue={filter.category ?? ""} className="input">
            <option value="">{t.all}</option>
            {categories.map((c) => <option key={c.slug} value={c.slug}>{name(c)}</option>)}
          </select>
        </label>
        <label>
          <span className="label">{t.sort}</span>
          <select name="sort" defaultValue={filter.sort} className="input">
            <option value="discount">{t.sortDiscount}</option>
            <option value="ending">{t.sortEnding}</option>
          </select>
        </label>
        <div className="flex items-end">
          <button className="btn-primary w-full">{t.filter}</button>
        </div>
      </form>

      {deals.length === 0 ? (
        <p className="card py-10 text-center text-stone-500">{t.noDeals}</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {deals.map((d) => {
            const label = promoLabel(d, t, lang);
            const price = d.promoType === "percent_off" && d.regularPriceUsd && d.percentOff
              ? d.regularPriceUsd * (1 - d.percentOff / 100)
              : d.promoType === "multibuy" ? null : d.promoPriceUsd;
            return (
              <article key={d.id} className="card flex flex-col">
                <div className="mb-2 flex items-start justify-between gap-2">
                  <div className="text-xs font-medium text-emerald-700">{lang === "ar" ? d.storeNameAr : d.storeNameEn}</div>
                  {label && <span className="rounded-full bg-rose-600 px-2 py-0.5 text-xs font-semibold text-white">{label}</span>}
                </div>
                <h2 className="font-semibold leading-snug">{(lang === "ar" ? d.nameAr || d.nameEn : d.nameEn || d.nameAr) || d.title}</h2>
                {d.brand && <div className="text-xs text-stone-500">{d.brand}</div>}
                <div className="mt-auto pt-3">
                  {price != null && (
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="text-xl font-bold tabular-nums">{usd(price)}</span>
                      <span className="text-xs text-stone-500">{lbp(price, market.usdLbpRate)}</span>
                    </div>
                  )}
                  {d.regularPriceUsd != null && (d.promoType === "price_cut" || d.promoType === "percent_off") && (
                    <div className="text-xs text-stone-400">{t.was} <s>{usd(d.regularPriceUsd)}</s></div>
                  )}
                  {d.regularPriceUsd != null && (d.promoType === "bogo" || d.promoType === "multibuy") && (
                    <div className="text-sm text-stone-600">{usd(d.regularPriceUsd)} <span className="text-xs text-stone-400">/ 1</span></div>
                  )}
                  <div className="mt-2 flex justify-between text-xs text-stone-500">
                    <span>{d.validUntil ? `${t.validUntil} ${d.validUntil}` : t.sitePriceNow}</span>
                    <span>{d.source === "flyer" ? t.fromFlyer : t.fromSite}</span>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </>
  );
}
