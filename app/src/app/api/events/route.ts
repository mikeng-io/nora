import { NextResponse } from "next/server";
import { checkSession } from "@/lib/require-session";
import { proxyEventsGet } from "@/lib/events-proxy";

// Fix C1 (operator, 2026-08-19): calls checkSession() directly, not the bare
// allow/deny requireApiSession() every other route used to use here — this
// route needs the resolved personId itself, mirroring api/memory/route.ts's
// own reasoning exactly. Before this fix, the ONLY gate was "signed in at
// all"; a client-supplied conversation_id was forwarded straight through to
// nora/web/api.py's events_list, which did zero scoping — any signed-in
// portal user could read any conversation's events by changing the URL.
//
// `personId` is now forced from the session, never taken from the client —
// `URLSearchParams.set` overwrites anything the caller tried to pass — and
// nora/web/api.py's events_list 400s without a personId that resolves to a
// known person. `conversation_id` is still forwarded through untouched (it
// is still client-supplied and still required by events_list), because
// there is no conversation-roster store this route can consult to verify
// the caller is actually a MEMBER of that conversation — see events_list's
// own docstring/comment in nora/web/api.py for the remaining, documented gap.
export async function GET(request: Request) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const url = new URL(request.url);
  url.searchParams.set("personId", check.personId);
  return proxyEventsGet("/app/v1/events", url.search);
}
