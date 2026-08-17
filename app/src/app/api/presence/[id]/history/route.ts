import { NextResponse } from "next/server";
import { requireApiSession } from "@/lib/require-api-session";

const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const denied = await requireApiSession();
  if (denied) return denied;

  const { id } = await params;
  const hours = new URL(request.url).searchParams.get("hours") ?? "24";
  const res = await fetch(
    `${NORA_API_BASE}/app/v1/presence/${encodeURIComponent(id)}/history?hours=${encodeURIComponent(hours)}`,
    { cache: "no-store" },
  );
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
