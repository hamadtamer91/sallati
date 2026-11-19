"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useBasket } from "@/lib/store";
import { useLang } from "./LangProvider";

export function Nav() {
  const { lang, t } = useLang();
  const path = usePathname();
  const router = useRouter();
  const { items } = useBasket();
  const links = [
    { href: "/basket", label: t.navBasket, badge: items.length || null },
    { href: "/compare", label: t.navCompare },
    { href: "/explore", label: t.navExplore },
    { href: "/admin/scrapers", label: t.navAdmin },
  ];
  const switchLang = () => {
    document.cookie = `lang=${lang === "ar" ? "en" : "ar"}; path=/; max-age=31536000; samesite=lax`;
    router.refresh();
  };
  return (
    <header className="sticky top-0 z-[1000] border-b border-stone-200 bg-white/90 backdrop-blur">
      <nav className="mx-auto flex max-w-6xl items-center gap-1 px-4 py-3">
        <Link href="/basket" className="me-4 text-xl font-bold text-emerald-700">
          {t.appName}
        </Link>
        <div className="flex flex-1 gap-1 overflow-x-auto">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={`whitespace-nowrap rounded-full px-3 py-1.5 text-sm ${
                path.startsWith(l.href) ? "bg-emerald-600 text-white" : "text-stone-700 hover:bg-stone-100"
              }`}
            >
              {l.label}
              {l.badge ? <span className="ms-1.5 rounded-full bg-amber-400 px-1.5 text-xs text-stone-900">{l.badge}</span> : null}
            </Link>
          ))}
        </div>
        <button onClick={switchLang} className="rounded-full border border-stone-300 px-3 py-1 text-sm hover:bg-stone-50">
          {lang === "ar" ? "EN" : "عربي"}
        </button>
      </nav>
    </header>
  );
}
