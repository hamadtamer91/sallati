export type DisplayCurrency = "USD" | "LBP" | "BOTH";

const usdFmt = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
const lbpFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function usd(v: number): string {
  return usdFmt.format(v);
}

export function lbp(v: number, rate: number): string {
  // LBP prices are rounded to the nearest 1,000 like on Lebanese shelves
  return `${lbpFmt.format(Math.round((v * rate) / 1000) * 1000)} ل.ل`;
}

export function money(v: number, cur: DisplayCurrency, rate: number): string {
  if (cur === "USD") return usd(v);
  if (cur === "LBP") return lbp(v, rate);
  return `${usd(v)} · ${lbp(v, rate)}`;
}

/** Price per litre in USD from a "safiha" (20 L) price in the user's currency. */
export function fuelPerLiterUsd(pricePer20L: number, currency: "USD" | "LBP", rate: number): number {
  const usd20 = currency === "USD" ? pricePer20L : pricePer20L / rate;
  return usd20 / 20;
}
