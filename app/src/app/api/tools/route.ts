import { NextResponse } from "next/server";
import { requireApiSession } from "@/lib/require-api-session";

// Server-only: NORA_API_BASE is never sent to the browser. The browser
// calls same-origin /api/tools; this route is the only thing that ever
// reaches Nora's :9100 directly — the same proxy posture every other
// /api/* route holds (design spec §6).
const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

export async function GET() {
  const denied = await requireApiSession();
  if (denied) return denied;

  // `cache: "no-store"`: the tool catalog is a LIVE view of the deployed
  // agent's registry — a tool added or renamed on the backend must appear
  // on the next page load without a frontend rebuild.
  const res = await fetch(`${NORA_API_BASE}/app/v1/tools`, { cache: "no-store" });
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
