import { NextResponse } from "next/server";
import { z } from "zod";
import { checkSession } from "@/server/session";
import { downloadRunner } from "@/server/downloads/runner";
import { convert } from "@/server/downloads/service";

const PRIVATE = { "cache-control": "private, no-store" };
const Body = z.object({ target: z.string().max(8) });

export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401, headers: PRIVATE });
  }
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "invalid body" }, { status: 400, headers: PRIVATE });
  }
  const out = await convert(downloadRunner(), (await params).id, parsed.data.target);
  return NextResponse.json(out.body, { status: out.status, headers: PRIVATE });
}
