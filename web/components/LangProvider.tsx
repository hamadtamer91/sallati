"use client";
import { createContext, useContext } from "react";
import { getDict, type Dict, type Lang } from "@/lib/i18n";

const Ctx = createContext<{ lang: Lang; t: Dict }>({ lang: "ar", t: getDict("ar") });

export function LangProvider({ lang, children }: { lang: Lang; children: React.ReactNode }) {
  return <Ctx.Provider value={{ lang, t: getDict(lang) }}>{children}</Ctx.Provider>;
}

export const useLang = () => useContext(Ctx);

/** Pick the product name in the UI language, falling back to the other one. */
export function pickName(lang: Lang, en: string | null | undefined, ar: string | null | undefined): string {
  return (lang === "ar" ? ar || en : en || ar) ?? "";
}
