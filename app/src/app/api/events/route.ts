import { requireApiSession } from "@/lib/require-api-session";
import { proxyEventsGet } from "@/lib/events-proxy";

// Browser calls same-origin /api/events?conversation_id=...; this reaches
// Nora's :9100 API the same way api/games/servers/[id]/sessions/route.ts
// does — conversation_id is required by nora/web/api.py's events_list (400
// if missing), so the query string is forwarded through untouched rather
// than re-parsed here.
export async function GET(request: Request) {
  const denied = await requireApiSession();
  if (denied) return denied;

  const search = new URL(request.url).search;
  return proxyEventsGet("/app/v1/events", search);
}
