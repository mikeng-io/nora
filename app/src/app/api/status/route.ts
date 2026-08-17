import { NextResponse } from "next/server";

// Server-only: NORA_API_BASE is never sent to the browser. The browser
// calls same-origin /api/status; this route is the only thing that ever
// reaches Nora's :9100 directly (design spec §6's "browser never talks to
// Nora's API directly — only to Next.js").
const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

export async function GET() {
  const res = await fetch(`${NORA_API_BASE}/app/v1/status`, { cache: "no-store" });
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
