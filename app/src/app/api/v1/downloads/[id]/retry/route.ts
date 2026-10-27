import { NextResponse } from "next/server";
import { checkSession } from "@/server/session";
import { downloadRunner } from "@/server/downloads/runner";
import { retry } from "@/server/downloads/service";
import { JOB_ID } from "@/server/downloads/store";

// Re-queue a failed job; a live or finished one is a 409, not a second download.
const PRIVATE = { "cache-control": "private, no-store" };

export async function POST(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401, headers: PRIVATE });
  }
  const { id } = await params;
  if (!JOB_ID.test(id)) {
    return NextResponse.json({ error: "not found" }, { status: 404, headers: PRIVATE });
  }
  const out = retry(downloadRunner(), id);
  return NextResponse.json(out.body, { status: out.status, headers: PRIVATE });
}
