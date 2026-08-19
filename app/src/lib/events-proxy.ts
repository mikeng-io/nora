import { NextResponse } from "next/server";

// Server-only: NORA_API_BASE is never sent to the browser. Mirrors
// games-proxy.ts's proxyGamesGet exactly — every app/api/events/**/route.ts
// is a thin same-origin proxy onto Nora's own :9100 API
// (nora/web/api.py's `/app/v1/events(/{id})` pair, backed locally by
// EventService/Postgres, not a remote REST API — see nora/web/events.py's
// module docstring).
const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

/** Proxies a GET to `${NORA_API_BASE}${path}${search}`, passing Nora's
 * status code and JSON body straight through — one place for every events
 * route to share instead of near-identical `fetch`+`NextResponse.json`
 * blocks. */
export async function proxyEventsGet(path: string, search: string): Promise<NextResponse> {
  const res = await fetch(`${NORA_API_BASE}${path}${search}`, { cache: "no-store" });
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
