import "server-only";
import { demoSource } from "./demo";
import { supabaseSource } from "./supabase";
import type { DataSource } from "./types";

export type * from "./types";

let cached: DataSource | null = null;

/** Supabase when configured, otherwise the built-in demo data. */
export function getDataSource(): DataSource {
  if (!cached) {
    const configured = process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    cached = configured ? supabaseSource() : demoSource;
  }
  return cached;
}
