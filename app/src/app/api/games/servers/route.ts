import { NextResponse } from "next/server";

// Server-only: GAMES_API_BASE is never sent to the browser. The browser
// calls same-origin /api/games/servers; this route is the only thing that
// ever reaches Pulse's tailnet address directly (design spec §5.3 — "web/
// container reaches it as a tailnet peer, not through Nora"). Same pattern
// as api/status/route.ts's NORA_API_BASE.
const GAMES_API_BASE =
  process.env.GAMES_API_BASE ?? "http://orion.tail0a1b2c3d.ts.net:8787";

export async function GET() {
  const res = await fetch(`${GAMES_API_BASE}/v1/servers`, { cache: "no-store" });
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
