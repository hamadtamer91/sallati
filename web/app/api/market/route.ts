import { NextResponse } from "next/server";
import { getDataSource } from "@/lib/data";

export const revalidate = 3600;

export async function GET() {
  const ds = getDataSource();
  return NextResponse.json({ ...(await ds.market()), mode: ds.mode });
}
