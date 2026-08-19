import { NextResponse } from "next/server";
import { checkSession } from "@/lib/require-session";
import { proxyEventsGet } from "@/lib/events-proxy";

// Fix C1 — same reasoning as api/events/route.ts: resolves personId from
// the session server-side and forwards it, rather than the bare
// requireApiSession() allow/deny gate this route used to have (which let
// any signed-in portal user read any event's RSVPs — a list of person_ids
// — by guessing/enumerating ids). nora/web/api.py's events_detail 400s
// without a personId that resolves to a known person.
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const search = `?personId=${encodeURIComponent(check.personId)}`;
  return proxyEventsGet(`/app/v1/events/${encodeURIComponent(id)}`, search);
}
