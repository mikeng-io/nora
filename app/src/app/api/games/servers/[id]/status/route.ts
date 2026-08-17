import { NextResponse } from "next/server";

const GAMES_API_BASE =
  process.env.GAMES_API_BASE ?? "http://orion.tail0a1b2c3d.ts.net:8787";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await fetch(`${GAMES_API_BASE}/v1/servers/${encodeURIComponent(id)}/status`, {
    cache: "no-store",
  });
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
