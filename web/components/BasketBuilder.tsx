"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { ProductSummary } from "@/lib/data/types";
import { money } from "@/lib/money";
import { useBasket, useSettings } from "@/lib/store";
import { pickName, useLang } from "./LangProvider";

export function ProductSearch({ rate }: { rate: number }) {
  const { lang, t } = useLang();
  const { items, set } = useBasket();
  const { settings } = useSettings();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<ProductSummary[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    const id = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await fetch(`/api/products?q=${encodeURIComponent(q)}`, { signal: ctrl.signal });
        setResults((await r.json()).products ?? []);
      } catch {
        /* aborted */
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => {
      clearTimeout(id);
      ctrl.abort();
    };
  }, [q]);

  const add = (p: ProductSummary) =>
    set((prev) =>
      prev.some((i) => i.productId === p.id)
        ? prev.map((i) => (i.productId === p.id ? { ...i, qty: i.qty + 1 } : i))
        : [...prev, { productId: p.id, nameEn: p.nameEn, nameAr: p.nameAr, sizeLabel: p.sizeLabel, qty: 1,
            allowSubstitutes: true, minPriceUsd: p.minPriceUsd }],
    );

  return (
    <section className="card">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder={t.searchPlaceholder}
        className="input text-base"
        autoFocus
      />
      <ul className="mt-3 max-h-[26rem] divide-y divide-stone-100 overflow-y-auto">
        {!loading && results.length === 0 && <li className="py-6 text-center text-stone-500">{t.noResults}</li>}
        {results.map((p) => {
          const inBasket = items.some((i) => i.productId === p.id);
          return (
            <li key={p.id} className="flex items-center gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{pickName(lang, p.nameEn, p.nameAr)}</div>
                <div className="text-xs text-stone-500">
                  {p.brand ? `${p.brand} · ` : ""}
                  {p.offersCount} {t.stores}
                  {p.minPriceUsd != null && <> · {t.fromPrice} {money(p.minPriceUsd, settings.displayCurrency, rate)}</>}
                </div>
              </div>
              <button onClick={() => add(p)} className={inBasket ? "btn-soft" : "btn-primary"}>
                {inBasket ? `${t.added} +1` : t.add}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function BasketList() {
  const { lang, t } = useLang();
  const { items, set } = useBasket();
  const update = (id: string, patch: Partial<(typeof items)[number]>) =>
    set((prev) => prev.map((i) => (i.productId === id ? { ...i, ...patch } : i)));

  return (
    <section className="card">
      <h2 className="mb-3 text-lg font-semibold">
        {t.basketTitle} <span className="text-stone-400">({items.length})</span>
      </h2>
      {items.length === 0 ? (
        <p className="py-6 text-center text-stone-500">{t.basketEmpty}</p>
      ) : (
        <ul className="divide-y divide-stone-100">
          {items.map((i) => (
            <li key={i.productId} className="py-2.5">
              <div className="flex items-center gap-2">
                <div className="min-w-0 flex-1 truncate font-medium">{pickName(lang, i.nameEn, i.nameAr)}</div>
                <div className="flex items-center rounded-full border border-stone-300">
                  <button aria-label="-" className="px-3 py-1" onClick={() => update(i.productId, { qty: Math.max(1, i.qty - 1) })}>−</button>
                  <span className="w-8 text-center tabular-nums">{i.qty}</span>
                  <button aria-label="+" className="px-3 py-1" onClick={() => update(i.productId, { qty: Math.min(99, i.qty + 1) })}>+</button>
                </div>
                <button className="text-sm text-rose-600 hover:underline" onClick={() => set((p) => p.filter((x) => x.productId !== i.productId))}>
                  {t.remove}
                </button>
              </div>
              <label className="mt-1 flex items-center gap-2 text-xs text-stone-500">
                <input type="checkbox" checked={i.allowSubstitutes} onChange={(e) => update(i.productId, { allowSubstitutes: e.target.checked })} />
                {t.allowSubs}
              </label>
            </li>
          ))}
        </ul>
      )}
      <Link
        href="/compare"
        aria-disabled={items.length === 0}
        className={`btn-primary mt-4 block text-center ${items.length === 0 ? "pointer-events-none opacity-40" : ""}`}
      >
        {t.compareBtn}
      </Link>
    </section>
  );
}
