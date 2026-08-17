import { NextResponse } from "next/server";
import { requireApiSession } from "@/lib/require-api-session";

const GAMES_API_BASE =
  process.env.GAMES_API_BASE ?? "http://orion.tail0a1b2c3d.ts.net:8787";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const denied = await requireApiSession();
  if (denied) return denied;

  const { id } = await params;
  const hours = new URL(request.url).searchParams.get("hours") ?? "24";
  const res = await fetch(
    `${GAMES_API_BASE}/v1/servers/${encodeURIComponent(id)}/graph?hours=${encodeURIComponent(hours)}`,
    { cache: "no-store" },
  );
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
