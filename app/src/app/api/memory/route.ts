import { NextResponse } from "next/server";
import { requireApiSession } from "@/lib/require-api-session";

const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

export async function GET() {
  const denied = await requireApiSession();
  if (denied) return denied;

  const res = await fetch(`${NORA_API_BASE}/app/v1/memory`, { cache: "no-store" });
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
