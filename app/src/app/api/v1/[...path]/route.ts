import { NextResponse } from "next/server";
import { checkSession } from "@/server/session";
import { resolveUpstream } from "@/server/upstream";

// Server-only: NORA_API_BASE never reaches the browser. This route is the
// only thing that talks to Nora's :9100 API (design spec §6), and it only
// forwards what src/server/upstream.ts's table allows.
const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";
const UPSTREAM_TIMEOUT_MS = 15_000;

const PRIVATE = { "cache-control": "private, no-store" };

export async function GET(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401, headers: PRIVATE });
  }

  const { path } = await params;
  const resolved = resolveUpstream(path, new URL(request.url).searchParams, check.personId);
  if (!resolved.ok) {
    return NextResponse.json({ error: "not found" }, { status: resolved.status, headers: PRIVATE });
  }

  let res: Response;
  try {
    res = await fetch(`${NORA_API_BASE}${resolved.upstreamPath}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    // Nora is down or slow: "I couldn't check", never "there's nothing".
    return NextResponse.json({ error: "unavailable" }, { status: 503, headers: PRIVATE });
  }

  const text = await res.text();
  try {
    return NextResponse.json(JSON.parse(text), { status: res.status, headers: PRIVATE });
  } catch {
    // A body that is not JSON (a proxy's HTML error page) is an outage too.
    return NextResponse.json({ error: "unavailable" }, { status: 502, headers: PRIVATE });
  }
}
