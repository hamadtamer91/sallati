import { NextResponse } from "next/server";
import { getDataSource } from "@/lib/data";

export async function GET(req: Request) {
  const q = new URL(req.url).searchParams.get("q")?.slice(0, 80) ?? "";
  try {
    const products = await getDataSource().searchProducts(q, 20);
    return NextResponse.json({ products });
  } catch (e) {
    console.error(e);
    return NextResponse.json({ error: "search_failed" }, { status: 500 });
  }
}
