import { NextResponse, type NextRequest } from "next/server";

/** Browser password prompt for /admin when ADMIN_PASSWORD is set (required in production with real data). */
export function middleware(req: NextRequest) {
  const password = process.env.ADMIN_PASSWORD;
  const supabase = !!process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (!password) {
    return supabase ? new NextResponse("Set ADMIN_PASSWORD to open the monitor.", { status: 503 }) : NextResponse.next();
  }
  const [scheme, encoded] = (req.headers.get("authorization") ?? "").split(" ");
  if (scheme === "Basic" && encoded) {
    const [user, pass] = atob(encoded).split(":");
    if (user === (process.env.ADMIN_USER || "admin") && pass === password) return NextResponse.next();
  }
  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Sallati admin"' },
  });
}

export const config = { matcher: ["/admin/:path*"] };
