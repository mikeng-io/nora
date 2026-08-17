import { NextResponse } from "next/server";
import { requireApiSession } from "@/lib/require-api-session";

// Server-only: NORA_API_BASE is never sent to the browser. The browser calls
// same-origin /api/games/servers; this route reaches Nora's :9100 API the
// same way api/status/route.ts and api/memory/route.ts already do — Nora's
// own MCP connection to Pulse does the actual reaching-across-the-tailnet
// (nora/web/games.py), which is what replaced the old direct-REST path this
// route used to take (GAMES_API_BASE -> Pulse's HTTP port), a path that
// never actually worked: that port is host-loopback-only by design and
// unreachable from this container's separate compose project.
const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

export async function GET() {
  const denied = await requireApiSession();
  if (denied) return denied;

  const res = await fetch(`${NORA_API_BASE}/app/v1/games/servers`, { cache: "no-store" });
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
