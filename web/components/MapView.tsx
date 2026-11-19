"use client";
import { useEffect, useRef } from "react";
import type { LatLng } from "@/lib/optimizer";
import "leaflet/dist/leaflet.css";

export interface MapMarker {
  lat: number;
  lng: number;
  label: string;
  color?: string;
  number?: number;
}

interface Props {
  center?: LatLng | null;
  markers?: MapMarker[];
  route?: LatLng[]; // polyline in visiting order
  onPick?: (p: LatLng) => void;
  className?: string;
}

const BEIRUT: LatLng = { lat: 33.8938, lng: 35.5018 };

/** Leaflet + OpenStreetMap tiles, loaded only in the browser. */
export default function MapView({ center, markers = [], route, onPick, className }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<any>(null);
  const layer = useRef<any>(null);
  const pickRef = useRef(onPick);
  pickRef.current = onPick;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || !el.current || map.current) return;
      map.current = L.map(el.current, { zoomControl: true }).setView(center ?? BEIRUT, 12);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap",
      }).addTo(map.current);
      layer.current = L.layerGroup().addTo(map.current);
      map.current.on("click", (e: any) => pickRef.current?.({ lat: e.latlng.lat, lng: e.latlng.lng }));
      draw(L);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (map.current) import("leaflet").then((m) => draw(m.default));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(markers), JSON.stringify(route), center?.lat, center?.lng]);

  useEffect(() => () => map.current?.remove(), []);

  function draw(L: any) {
    if (!layer.current) return;
    layer.current.clearLayers();
    const pts: [number, number][] = [];
    if (center) {
      L.circleMarker([center.lat, center.lng], { radius: 9, color: "#065f46", fillColor: "#10b981", fillOpacity: 1 })
        .bindTooltip("🏠")
        .addTo(layer.current);
      pts.push([center.lat, center.lng]);
    }
    for (const m of markers) {
      const icon = L.divIcon({
        className: "",
        html: `<div style="background:${m.color ?? "#d97706"};color:#fff;border-radius:9999px;width:26px;height:26px;display:flex;align-items:center;justify-content:center;font:600 13px system-ui;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35)">${m.number ?? "•"}</div>`,
        iconSize: [26, 26],
        iconAnchor: [13, 13],
      });
      L.marker([m.lat, m.lng], { icon }).bindTooltip(m.label).addTo(layer.current);
      pts.push([m.lat, m.lng]);
    }
    if (route && route.length > 1) {
      L.polyline(route.map((p) => [p.lat, p.lng]), { color: "#059669", weight: 4, dashArray: "6 6" }).addTo(layer.current);
    }
    if (pts.length > 1) map.current.fitBounds(pts, { padding: [30, 30], maxZoom: 14 });
    else if (pts.length === 1) map.current.setView(pts[0], 13);
  }

  return <div ref={el} className={className ?? "h-72 w-full rounded-xl"} />;
}
