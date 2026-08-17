import { NextResponse } from "next/server";
import { checkSession } from "@/lib/require-session";

// Every app/api/* proxy route calls this first. Returns a 401 response to
// return immediately, or null to proceed — matches (portal)/layout.tsx's
// gate exactly (same checkSession()), just as a response instead of a
// redirect, since an API route has no page to redirect to.
export async function requireApiSession(): Promise<NextResponse | null> {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  return null;
}
