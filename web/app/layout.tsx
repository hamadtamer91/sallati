import type { Metadata } from "next";
import { LangProvider } from "@/components/LangProvider";
import { Nav } from "@/components/Nav";
import { getDataSource } from "@/lib/data";
import { getDict } from "@/lib/i18n";
import { getLang } from "@/lib/lang";
import "./globals.css";

export const metadata: Metadata = {
  title: "سلّتي — Sallati",
  description: "قارن أسعار السوبرماركت في لبنان مع حساب البنزين وصافي التوفير",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const lang = await getLang();
  const t = getDict(lang);
  const demo = getDataSource().mode === "demo";
  return (
    <html lang={lang} dir={lang === "ar" ? "rtl" : "ltr"}>
      <body className="min-h-screen">
        <LangProvider lang={lang}>
          <Nav />
          {demo && <div className="bg-amber-100 px-4 py-1.5 text-center text-xs text-amber-900">{t.demoBanner}</div>}
          <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
        </LangProvider>
      </body>
    </html>
  );
}
