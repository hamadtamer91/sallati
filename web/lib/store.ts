"use client";
import { useSyncExternalStore } from "react";
import type { DisplayCurrency } from "./money";
import type { LatLng } from "./optimizer";

export interface BasketLine {
  productId: string;
  nameEn: string | null;
  nameAr: string | null;
  sizeLabel: string;
  qty: number;
  allowSubstitutes: boolean;
  minPriceUsd: number | null;
}

export interface Settings {
  origin: LatLng | null;
  fuelPricePer20L: number | null; // null = official price
  fuelCurrency: "LBP" | "USD";
  consumption: number;
  maxStores: number;
  radiusKm: number;
  displayCurrency: DisplayCurrency;
}

export const DEFAULT_SETTINGS: Settings = {
  origin: null,
  fuelPricePer20L: null,
  fuelCurrency: "LBP",
  consumption: 9,
  maxStores: 3,
  radiusKm: 15,
  displayCurrency: "USD",
};

/** Tiny persisted store shared by all components (localStorage, safe on the server). */
function persistent<T>(key: string, initial: T) {
  let value: T | undefined;
  const listeners = new Set<() => void>();
  const read = (): T => {
    if (value !== undefined) return value;
    try {
      const raw = window.localStorage.getItem(key);
      value = raw ? { ...initial, ...JSON.parse(raw) } : initial;
      if (Array.isArray(initial) && raw) value = JSON.parse(raw);
    } catch {
      value = initial;
    }
    return value!;
  };
  return {
    get: read,
    set(next: T | ((prev: T) => T)) {
      value = typeof next === "function" ? (next as (p: T) => T)(read()) : next;
      try {
        window.localStorage.setItem(key, JSON.stringify(value));
      } catch {
        /* storage full or blocked: keep in memory */
      }
      listeners.forEach((l) => l());
    },
    subscribe(l: () => void) {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    server: () => initial,
  };
}

export const basketStore = persistent<BasketLine[]>("sallati.basket.v1", []);
export const settingsStore = persistent<Settings>("sallati.settings.v1", DEFAULT_SETTINGS);

export function useBasket() {
  const items = useSyncExternalStore(basketStore.subscribe, basketStore.get, basketStore.server);
  return { items, set: basketStore.set };
}

export function useSettings() {
  const settings = useSyncExternalStore(settingsStore.subscribe, settingsStore.get, settingsStore.server);
  return { settings, set: (patch: Partial<Settings>) => settingsStore.set((s) => ({ ...s, ...patch })) };
}
