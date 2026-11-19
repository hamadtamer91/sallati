"use client";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { fmtNote } from "@/lib/i18n";
import { money } from "@/lib/money";
import type { OptimizeResult, Plan, ScenarioKind } from "@/lib/optimizer";
import { useBasket, useSettings } from "@/lib/store";
import { useLang } from "./LangProvider";

const MapView = dynamic(() => import("./MapView"), { ssr: false, loading: () => <div className="h-80 animate-pulse rounded-xl bg-stone-100" /> });
const COLORS = ["#d97706", "#2563eb", "#9333ea", "#dc2626", "#0891b2"];

interface Response { result: OptimizeResult; rate: number; fuelPerLiterUsd: number }

export function CompareView() {
  const { t } = useLang();
  const { items } = useBasket();
  const { settings } = useSettings();
  const [data, setData] = useState<Response | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<ScenarioKind | null>(null);
  const key = JSON.stringify([items, settings]);

  useEffect(() => {
    if (!items.length || !settings.origin) return;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    fetch("/api/optimize", {
      method: "POST",
      signal: ctrl.signal,
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        items: items.map((i) => ({ productId: i.productId, name: i.nameAr || i.nameEn, qty: i.qty, allowSubstitutes: i.allowSubstitutes })),
        origin: settings.origin,
        fuel: { pricePer20L: settings.fuelPricePer20L ?? 0, currency: settings.fuelCurrency, consumptionLPer100km: settings.consumption },
        options: { maxStores: settings.maxStores, radiusKm: settings.radiusKm },
      }),
    })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json()).error ?? r.statusText);
        return r.json();
      })
      .then((d: Response) => {
        setData(d);
        setSelected(d.result.recommended);
      })
      .catch((e) => e.name !== "AbortError" && setError(String(e.message)))
      .finally(() => setLoading(false));
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (!items.length) return <Empty text={t.needBasket} />;
  if (!settings.origin) return <Empty text={t.needLocation} />;
  if (loading && !data) return <div className="card animate-pulse py-16 text-center text-stone-500">{t.calculating}</div>;
  if (error) return <Empty text={`${t.errorTryAgain} (${error})`} />;
  if (!data) return null;

  const { result, rate } = data;
  const plans = (["single", "split", "deals"] as const).map((k) => result[k]).filter((p): p is Plan => !!p);
  const active = plans.find((p) => p.kind === selected) ?? plans[0];

  return (
    <div className="space-y-5">
      {result.unavailable.length > 0 && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm">
          <b>{t.unavailable}:</b> {result.unavailable.map((u) => u.name).join("، ")}
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-3">
        {plans.map((p) => (
          <ScenarioCard key={p.kind} plan={p} rate={rate} recommended={result.recommended === p.kind}
            selected={active?.kind === p.kind} onSelect={() => setSelected(p.kind)} />
        ))}
      </div>
      {active && <PlanDetails plan={active} rate={rate} />}
      <p className="text-xs text-stone-400">
        {result.meta.branchesConsidered} branches · {result.meta.subsetsEvaluated} combinations · {result.meta.ms} ms · fuel ${data.fuelPerLiterUsd.toFixed(3)}/L
      </p>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  const { t } = useLang();
  return (
    <div className="card py-14 text-center">
      <p className="mb-4 text-stone-600">{text}</p>
      <Link href="/basket" className="btn-primary">{t.navBasket}</Link>
    </div>
  );
}

function ScenarioCard({ plan, rate, recommended, selected, onSelect }: {
  plan: Plan; rate: number; recommended: boolean; selected: boolean; onSelect: () => void;
}) {
  const { lang, t } = useLang();
  const { settings } = useSettings();
  const m = (v: number) => money(v, settings.displayCurrency, rate);
  const title = { single: t.single, split: t.split, deals: t.deals }[plan.kind];
  const desc = { single: t.singleDesc, split: t.splitDesc, deals: t.dealsDesc }[plan.kind];
  const positive = plan.netSavingsUsd >= 0;

  return (
    <button
      onClick={onSelect}
      className={`card relative text-start transition ${selected ? "ring-2 ring-emerald-500" : "hover:ring-1 hover:ring-stone-300"} ${
        plan.worthIt ? "" : "opacity-70"}`}
    >
      <div className="mb-1 flex items-center gap-2">
        <h3 className="text-lg font-semibold">{title}</h3>
        {recommended && <span className="rounded-full bg-emerald-600 px-2 py-0.5 text-xs text-white">{t.recommended}</span>}
        {!plan.worthIt && <span className="rounded-full bg-stone-200 px-2 py-0.5 text-xs">{t.notWorth}</span>}
      </div>
      <p className="mb-3 text-xs text-stone-500">{desc}</p>
      <div className={`text-2xl font-bold ${positive ? "text-emerald-700" : "text-rose-600"}`}>
        {positive ? "+" : ""}{m(plan.netSavingsUsd)}
      </div>
      <div className="text-xs text-stone-500">{t.netSavings} {t.vsAverage} ({plan.netSavingsPct}%)</div>
      <dl className="mt-3 grid grid-cols-2 gap-y-1 text-sm">
        <dt className="text-stone-500">{t.basketCost}</dt><dd className="text-end tabular-nums">{m(plan.basketCostUsd)}</dd>
        <dt className="text-stone-500">{t.fuelCost}</dt>
        <dd className="text-end tabular-nums">{m(plan.fuelCostUsd)} <span className="text-xs text-stone-400">({plan.distanceKm} {t.km})</span></dd>
        <dt className="font-medium">{t.total}</dt><dd className="text-end font-semibold tabular-nums">{m(plan.totalCostUsd)}</dd>
        {plan.savingsVsSingleUsd != null && (
          <><dt className="text-stone-500">{t.vsSingle}</dt><dd className="text-end tabular-nums">{m(plan.savingsVsSingleUsd)}</dd></>
        )}
        {plan.dealSavingsUsd > 0 && (
          <><dt className="text-stone-500">{t.dealSavings}</dt><dd className="text-end tabular-nums">{m(plan.dealSavingsUsd)}</dd></>
        )}
      </dl>
      <div className="mt-3 text-xs text-stone-600">{plan.stops.map((s) => s.branchName).join(lang === "ar" ? " ← " : " → ")}</div>
      {plan.notes.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-amber-700">
          {plan.notes.map((n, i) => <li key={i}>• {fmtNote(t, n.code, n.params)}</li>)}
        </ul>
      )}
    </button>
  );
}

function PlanDetails({ plan, rate }: { plan: Plan; rate: number }) {
  const { t } = useLang();
  const { settings } = useSettings();
  const m = (v: number) => money(v, settings.displayCurrency, rate);
  const markers = useMemo(
    () => plan.stops.map((s, i) => ({ lat: s.lat, lng: s.lng, label: s.branchName, number: s.order, color: COLORS[i % COLORS.length] })),
    [plan],
  );
  const route = settings.origin ? [settings.origin, ...plan.stops.map((s) => ({ lat: s.lat, lng: s.lng })), settings.origin] : [];

  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <div className="space-y-3 lg:col-span-3">
        {plan.stops.map((s, i) => (
          <div key={s.branchId} className="card">
            <div className="mb-2 flex items-center gap-2">
              <span className="flex h-7 w-7 items-center justify-center rounded-full text-sm font-semibold text-white" style={{ background: COLORS[i % COLORS.length] }}>
                {s.order}
              </span>
              <div className="flex-1">
                <div className="font-semibold">{s.storeName}</div>
                <div className="text-xs text-stone-500">{s.branchName} · {s.legKm} {t.km}</div>
              </div>
              <div className="font-semibold tabular-nums">{m(s.subtotalUsd)}</div>
            </div>
            <table className="w-full text-sm">
              <tbody>
                {s.lines.map((l) => (
                  <tr key={l.productId} className="border-t border-stone-100">
                    <td className="py-1.5">
                      {l.name}
                      {l.substituteFor && <div className="text-xs text-sky-700">↺ {t.substitute} {l.substituteFor.name}</div>}
                      {l.promo && <span className="ms-2 rounded bg-amber-100 px-1.5 text-xs text-amber-800">{l.promo.replace("_", " ")}</span>}
                      {l.extraFreeUnits ? <span className="ms-1 text-xs text-emerald-700">+{l.extraFreeUnits} {t.free}</span> : null}
                    </td>
                    <td className="w-12 whitespace-nowrap px-2 text-center tabular-nums text-stone-500">×{l.qty}</td>
                    <td className="whitespace-nowrap text-end tabular-nums">
                      {l.regularCostUsd > l.costUsd + 0.004 && <s className="block text-xs text-stone-400">{m(l.regularCostUsd)}</s>}
                      {m(l.costUsd)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        {plan.missing.length > 0 && (
          <div className="card border-rose-200 bg-rose-50 text-sm">
            <b>{t.missing}:</b> {plan.missing.map((x) => `${x.name} ×${x.qty}`).join("، ")}
          </div>
        )}
      </div>
      <div className="lg:col-span-2">
        <div className="card sticky top-20">
          <h3 className="mb-2 font-semibold">{t.route}</h3>
          <MapView center={settings.origin} markers={markers} route={route} className="h-80 w-full rounded-xl" />
          <p className="mt-2 text-sm text-stone-600">
            {t.distance}: {plan.distanceKm} {t.km} · {plan.fuelLiters} {t.liters} · ~{plan.driveMinutes} {t.min}
          </p>
        </div>
      </div>
    </div>
  );
}
