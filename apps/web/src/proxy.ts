import { NextResponse, type NextRequest } from "next/server";

/**
 * Optimistic redirect to /login when there is no session cookie. This is a UX convenience
 * only: every API call is authenticated and authorized by the API itself.
 */
export function proxy(request: NextRequest) {
  if (request.cookies.has("nimbrix_session")) return NextResponse.next();
  const next = request.nextUrl.pathname + request.nextUrl.search;
  const url = new URL("/login", request.url);
  if (next !== "/") url.searchParams.set("next", next);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!login|healthz|status|api/|_next/|favicon.ico).*)"],
};
