import { requireApiSession } from "@/lib/require-api-session";
import { proxyGamesGet } from "@/lib/games-proxy";

// Browser calls same-origin /api/games/servers; this reaches Nora's :9100
// API the same way api/status/route.ts and api/memory/route.ts already do
// — Nora's own REST client to Pulse does the actual reaching-across-the-
// tailnet (nora/web/games.py), which is what replaced the old direct-REST
// path this route used to take (GAMES_API_BASE -> Pulse's HTTP port), a
// path that never actually worked: that port is host-loopback-only by
// design and unreachable from this container's separate compose project.
export async function GET() {
  const denied = await requireApiSession();
  if (denied) return denied;
  return proxyGamesGet("/app/v1/games/servers", "");
}
