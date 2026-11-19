import { getDataSource } from "@/lib/data";
import { getDict } from "@/lib/i18n";
import { getLang } from "@/lib/lang";

export const dynamic = "force-dynamic";

const STATUS: Record<string, string> = {
  success: "bg-emerald-100 text-emerald-800",
  done: "bg-emerald-100 text-emerald-800",
  partial: "bg-amber-100 text-amber-800",
  needs_review: "bg-amber-100 text-amber-800",
  running: "bg-sky-100 text-sky-800",
  processing: "bg-sky-100 text-sky-800",
  queued: "bg-stone-100 text-stone-700",
  failed: "bg-rose-100 text-rose-800",
};

const when = (iso: string) => new Date(iso).toLocaleString("en-GB", { dateStyle: "short", timeStyle: "short", timeZone: "Asia/Beirut" });

export default async function ScraperMonitor() {
  const t = getDict(await getLang());
  const ds = getDataSource();
  let runs: Awaited<ReturnType<typeof ds.runs>> = [];
  let flyers: Awaited<ReturnType<typeof ds.flyers>> = [];
  let error: string | null = null;
  try {
    [runs, flyers] = await Promise.all([ds.runs(50), ds.flyers(30)]);
  } catch (e) {
    error = String((e as Error).message);
  }

  // latest run per store+kind = current health
  const latest = new Map<string, (typeof runs)[number]>();
  for (const r of runs) if (!latest.has(`${r.store}|${r.kind}`)) latest.set(`${r.store}|${r.kind}`, r);
  const current = [...latest.values()];
  const failing = current.filter((r) => r.status !== "success");

  return (
    <>
      <h1 className="mb-4 text-2xl font-bold">{t.adminTitle}</h1>
      {error && <p className="card mb-4 border-rose-200 bg-rose-50 text-sm text-rose-800">{error}</p>}

      <div className="mb-5 grid gap-3 sm:grid-cols-4">
        <Stat label="OK" value={current.length - failing.length} tone="text-emerald-700" />
        <Stat label="⚠" value={failing.length} tone={failing.length ? "text-rose-600" : "text-stone-400"} />
        <Stat label={t.items} value={current.filter((r) => r.kind === "catalog").reduce((s, r) => s + r.itemsUpserted, 0)} />
        <Stat label={t.flyers} value={flyers.length} />
      </div>

      {failing.length > 0 && (
        <div className="card mb-5 border-amber-300 bg-amber-50">
          <h2 className="mb-2 font-semibold">{t.alerts}</h2>
          <ul className="space-y-1 text-sm">
            {failing.flatMap((r) => r.alerts.map((a, i) => (
              <li key={`${r.id}-${i}`}>
                <b>{r.store}</b> ({r.kind}) — <span className={a.severity === "critical" ? "text-rose-700" : "text-amber-700"}>{a.message}</span>
              </li>
            )))}
          </ul>
        </div>
      )}

      <section className="card mb-5 overflow-x-auto">
        <h2 className="mb-3 font-semibold">{t.runs}</h2>
        <table className="w-full min-w-[760px] text-sm [&_td]:px-2 [&_th]:px-2">
          <thead className="text-start text-xs text-stone-500">
            <tr>
              <th className="py-2 text-start">#</th><th className="text-start">{t.store}</th><th className="text-start">{t.kind}</th>
              <th className="text-start">{t.status}</th><th className="text-end">{t.items}</th><th className="text-end">{t.usual}</th>
              <th className="text-end">{t.failed}</th><th className="text-end">{t.newProducts}</th><th className="text-end">{t.started}</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="border-t border-stone-100">
                <td className="py-2 text-stone-400">{r.id}</td>
                <td>{r.store}<div className="text-xs text-stone-400">{r.adapter}</div></td>
                <td>{r.kind}</td>
                <td><span className={`rounded-full px-2 py-0.5 text-xs ${STATUS[r.status]}`}>{r.status}</span></td>
                <td className="text-end tabular-nums">{r.itemsSeen.toLocaleString("en-US")}</td>
                <td className="text-end tabular-nums text-stone-500">{r.baseline?.toLocaleString("en-US") ?? "—"}</td>
                <td className={`text-end tabular-nums ${r.itemsFailed ? "text-rose-600" : ""}`}>{r.itemsFailed}</td>
                <td className="text-end tabular-nums">{r.newProducts}</td>
                <td className="text-end text-xs text-stone-500" dir="ltr">{when(r.startedAt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card overflow-x-auto">
        <h2 className="mb-3 font-semibold">{t.flyers}</h2>
        <table className="w-full min-w-[760px] text-sm [&_td]:px-2 [&_th]:px-2">
          <thead className="text-xs text-stone-500">
            <tr>
              <th className="py-2 text-start">{t.store}</th><th className="text-start">file</th><th className="text-start">{t.status}</th>
              <th className="text-end">{t.pages}</th><th className="text-end">{t.extracted}</th><th className="text-end">{t.accepted}</th>
              <th className="text-end">{t.llmCalls}</th><th className="text-end">{t.validity}</th>
            </tr>
          </thead>
          <tbody>
            {flyers.map((f) => (
              <tr key={f.id} className="border-t border-stone-100">
                <td className="py-2">{f.store}</td>
                <td className="max-w-[14rem] truncate" title={f.error ?? f.sourceUrl ?? ""}>
                  {f.title ?? "—"}{f.error && <div className="text-xs text-rose-600">{f.error}</div>}
                </td>
                <td><span className={`rounded-full px-2 py-0.5 text-xs ${STATUS[f.status]}`}>{f.status}</span></td>
                <td className="text-end tabular-nums">{f.pages ?? "—"}</td>
                <td className="text-end tabular-nums">{f.extracted}</td>
                <td className="text-end tabular-nums">{f.accepted}</td>
                <td className="text-end tabular-nums">{f.llmCalls}</td>
                <td className="text-end text-xs text-stone-500" dir="ltr">{f.validFrom ? `${f.validFrom} → ${f.validUntil}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function Stat({ label, value, tone = "text-stone-800" }: { label: string; value: number; tone?: string }) {
  return (
    <div className="card">
      <div className="text-xs text-stone-500">{label}</div>
      <div className={`text-2xl font-bold tabular-nums ${tone}`}>{value.toLocaleString("en-US")}</div>
    </div>
  );
}
