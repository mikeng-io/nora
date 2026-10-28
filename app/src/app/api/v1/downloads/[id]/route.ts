import { NextResponse } from "next/server";
import { checkSession } from "@/server/session";
import { downloadRunner } from "@/server/downloads/runner";
import { get, remove } from "@/server/downloads/service";
import { JOB_ID } from "@/server/downloads/store";

const PRIVATE = { "cache-control": "private, no-store" };

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401, headers: PRIVATE });
  }
  const out = await get(downloadRunner(), (await params).id);
  return NextResponse.json(out.body, { status: out.status, headers: PRIVATE });
}

export async function DELETE(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401, headers: PRIVATE });
  }
  const { id } = await params;
  if (!JOB_ID.test(id)) {
    return NextResponse.json({ error: "not found" }, { status: 404, headers: PRIVATE });
  }
  const out = await remove(downloadRunner(), id);
  return out.status === 204
    ? new Response(null, { status: 204, headers: PRIVATE })
    : NextResponse.json(out.body, { status: out.status, headers: PRIVATE });
}
