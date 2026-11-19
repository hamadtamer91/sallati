"use client";
import dynamic from "next/dynamic";
import { useState } from "react";
import type { MarketSettings } from "@/lib/data/types";
import { lbp, usd } from "@/lib/money";
import { useSettings } from "@/lib/store";
import { useLang } from "./LangProvider";

const MapView = dynamic(() => import("./MapView"), { ssr: false, loading: () => <div className="h-56 animate-pulse rounded-xl bg-stone-100" /> });

export function SettingsPanel({ market }: { market: MarketSettings }) {
  const { t } = useLang();
  const { settings: s, set } = useSettings();
  const [locating, setLocating] = useState(false);
  const official = market.fuel95Per20lLbp;

  const locate = () => {
    if (!navigator.geolocation) return;
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (p) => {
        set({ origin: { lat: p.coords.latitude, lng: p.coords.longitude } });
        setLocating(false);
      },
      () => setLocating(false),
      { enableHighAccuracy: false, timeout: 10000 },
    );
  };

  return (
    <section className="card space-y-4">
      <h2 className="text-lg font-semibold">{t.settings}</h2>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="label">{t.location}</span>
          <button className="btn-soft text-xs" onClick={locate} disabled={locating}>
            {locating ? "…" : t.useMyLocation}
          </button>
        </div>
        <MapView center={s.origin} onPick={(p) => set({ origin: p })} className="h-56 w-full rounded-xl" />
        <p className="mt-1 text-xs text-stone-500">{t.clickMap}</p>
      </div>

      <div>
        <span className="label">{t.fuelPrice}</span>
        <div className="flex gap-2">
          <input
            type="number"
            inputMode="decimal"
            className="input"
            placeholder={official ? String(official) : ""}
            value={s.fuelPricePer20L ?? ""}
            onChange={(e) => set({ fuelPricePer20L: e.target.value ? Number(e.target.value) : null })}
          />
          <select className="input w-24" value={s.fuelCurrency} onChange={(e) => set({ fuelCurrency: e.target.value as "LBP" | "USD" })}>
            <option value="LBP">LBP</option>
            <option value="USD">USD</option>
          </select>
        </div>
        {official && (
          <p className="mt-1 text-xs text-stone-500">
            {t.officialPrice} 95: {lbp(official / market.usdLbpRate, market.usdLbpRate)} ≈ {usd(official / market.usdLbpRate)}
            {market.fuelEffectiveDate ? ` (${market.fuelEffectiveDate})` : ""} · {t.rate}: {market.usdLbpRate.toLocaleString("en-US")}
          </p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label>
          <span className="label">{t.consumption}</span>
          <input type="number" min={2} max={40} step={0.5} className="input" value={s.consumption}
            onChange={(e) => set({ consumption: Number(e.target.value) || 9 })} />
        </label>
        <label>
          <span className="label">{t.radius}</span>
          <input type="number" min={1} max={60} className="input" value={s.radiusKm}
            onChange={(e) => set({ radiusKm: Number(e.target.value) || 15 })} />
        </label>
        <label>
          <span className="label">{t.maxStores}</span>
          <select className="input" value={s.maxStores} onChange={(e) => set({ maxStores: Number(e.target.value) })}>
            {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label>
          <span className="label">{t.displayCurrency}</span>
          <select className="input" value={s.displayCurrency} onChange={(e) => set({ displayCurrency: e.target.value as any })}>
            <option value="USD">USD $</option>
            <option value="LBP">LBP ل.ل</option>
            <option value="BOTH">{t.both}</option>
          </select>
        </label>
      </div>
    </section>
  );
}
