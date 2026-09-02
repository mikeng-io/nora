import { NextResponse } from "next/server";
import { requireApiSession } from "@/lib/require-api-session";

const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

export async function GET(request: Request) {
  const denied = await requireApiSession();
  if (denied) return denied;

  const source = new URL(request.url).searchParams;
  const params = new URLSearchParams();
  for (const key of ["subjectId", "days"]) {
    const value = source.get(key);
    if (value !== null) params.set(key, value);
  }
  const query = params.toString();
  const res = await fetch(
    `${NORA_API_BASE}/app/v1/presence/analytics/rhythm${query ? `?${query}` : ""}`,
    { cache: "no-store" },
  );
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
