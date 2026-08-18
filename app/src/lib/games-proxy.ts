import { NextResponse } from "next/server";

// Server-only: NORA_API_BASE is never sent to the browser. Every
// app/api/games/servers/**/route.ts is a thin same-origin proxy onto
// Nora's own :9100 API (nora/web/api.py's `/app/v1/games/*` routes, which
// are themselves a thin pass-through onto Pulse's REST API) — see
// api/games/servers/route.ts's own comment for why this indirection
// exists (Pulse's port is host-loopback-only, unreachable from this
// container's separate compose project; Nora already holds the
// PULSE_API_TOKEN bearer credential and reaches across the tailnet).
const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

/** Proxies a GET to `${NORA_API_BASE}${path}${search}`, passing Nora's
 * status code and JSON body straight through — one place for every games
 * route to share instead of ten near-identical `fetch`+`NextResponse.json`
 * blocks. */
export async function proxyGamesGet(path: string, search: string): Promise<NextResponse> {
  const res = await fetch(`${NORA_API_BASE}${path}${search}`, { cache: "no-store" });
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
