import { BasketList, ProductSearch } from "@/components/BasketBuilder";
import { SettingsPanel } from "@/components/SettingsPanel";
import { getDataSource } from "@/lib/data";
import { getDict } from "@/lib/i18n";
import { getLang } from "@/lib/lang";

export default async function BasketPage() {
  const t = getDict(await getLang());
  const market = await getDataSource().market();
  return (
    <>
      <p className="mb-5 text-stone-600">{t.tagline}</p>
      <div className="grid gap-5 lg:grid-cols-5">
        <div className="space-y-5 lg:col-span-3">
          <ProductSearch rate={market.usdLbpRate} />
          <SettingsPanel market={market} />
        </div>
        <div className="lg:col-span-2">
          <div className="lg:sticky lg:top-20">
            <BasketList />
          </div>
        </div>
      </div>
    </>
  );
}
