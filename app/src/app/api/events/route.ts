import { NextResponse } from "next/server";
import { checkSession } from "@/lib/require-session";
import { proxyEventsGet } from "@/lib/events-proxy";

// Calls checkSession() directly, not the bare allow/deny requireApiSession()
// every other route used to use here — this route needs the resolved
// personId itself, mirroring api/memory/route.ts's own reasoning exactly.
//
// `personId` is forced from the session, never taken from the client — no
// other query params matter: nora/web/api.py's events_list is now
// participant-scoped, not conversation-scoped, so there is nothing left
// for a client-supplied param to filter or leak.
export async function GET() {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const search = new URLSearchParams({ personId: check.personId });
  return proxyEventsGet("/app/v1/events", search.toString());
}
