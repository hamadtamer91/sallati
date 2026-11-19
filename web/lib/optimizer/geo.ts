import type { LatLng } from "./types";

const R = 6371.0088; // mean Earth radius, km
const rad = (d: number) => (d * Math.PI) / 180;

/** Great-circle distance (haversine):
 *  a = sin²(Δφ/2) + cos φ1 · cos φ2 · sin²(Δλ/2);  d = 2R · atan2(√a, √(1−a)) */
export function haversineKm(a: LatLng, b: LatLng): number {
  const dLat = rad(b.lat - a.lat);
  const dLng = rad(b.lng - a.lng);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}

/** Road distance estimate. Lebanese roads wind; 1.3–1.5× straight line is typical.
 *  Replace with a routing matrix (OSRM / Mapbox) through options.distanceKm when available. */
export function roadKm(a: LatLng, b: LatLng, factor: number): number {
  return haversineKm(a, b) * factor;
}

function permutations<T>(xs: T[]): T[][] {
  if (xs.length <= 1) return [xs.slice()];
  const out: T[][] = [];
  xs.forEach((x, i) => {
    for (const p of permutations([...xs.slice(0, i), ...xs.slice(i + 1)])) out.push([x, ...p]);
  });
  return out;
}

export interface Route {
  order: number[]; // indexes into the stops array, in visiting order
  legsKm: number[]; // origin->first, first->second, ...
  returnKm: number;
  totalKm: number;
}

/** Shortest closed (or open) tour origin -> stops -> origin.
 *  Exact by enumeration up to 7 stops (5040 orders); nearest-neighbour + 2-opt beyond. */
export function bestRoute(
  origin: LatLng,
  stops: LatLng[],
  dist: (a: LatLng, b: LatLng) => number,
  returnHome = true,
): Route {
  if (stops.length === 0) return { order: [], legsKm: [], returnKm: 0, totalKm: 0 };
  const evalOrder = (order: number[]): Route => {
    const legs: number[] = [];
    let prev = origin;
    for (const i of order) {
      legs.push(dist(prev, stops[i]));
      prev = stops[i];
    }
    const back = returnHome ? dist(prev, origin) : 0;
    return { order, legsKm: legs, returnKm: back, totalKm: legs.reduce((s, x) => s + x, 0) + back };
  };
  const idx = stops.map((_, i) => i);
  if (stops.length <= 7) {
    let best: Route | null = null;
    for (const p of permutations(idx)) {
      const r = evalOrder(p);
      if (!best || r.totalKm < best.totalKm - 1e-9) best = r;
    }
    return best!;
  }
  // nearest neighbour
  const left = new Set(idx);
  const order: number[] = [];
  let cur = origin;
  while (left.size) {
    let bi = -1;
    let bd = Infinity;
    for (const i of left) {
      const d = dist(cur, stops[i]);
      if (d < bd) [bi, bd] = [i, d];
    }
    order.push(bi);
    left.delete(bi);
    cur = stops[bi];
  }
  // 2-opt
  let best = evalOrder(order);
  let improved = true;
  while (improved) {
    improved = false;
    for (let i = 0; i < order.length - 1; i++) {
      for (let k = i + 1; k < order.length; k++) {
        const cand = [...best.order.slice(0, i), ...best.order.slice(i, k + 1).reverse(), ...best.order.slice(k + 1)];
        const r = evalOrder(cand);
        if (r.totalKm < best.totalKm - 1e-9) {
          best = r;
          improved = true;
        }
      }
    }
  }
  return best;
}
