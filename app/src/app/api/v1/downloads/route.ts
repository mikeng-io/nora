import { NextResponse } from "next/server";
import { checkSession } from "@/server/session";
import { downloadRunner } from "@/server/downloads/runner";
import { SubmitSchema, list, submit } from "@/server/downloads/service";

// The circle's downloads (one shared library): submit a batch of URLs, list every
// job. These are LOCAL routes — they never touch Nora's :9100 API; the rules live
// in src/server/downloads/*.
const PRIVATE = { "cache-control": "private, no-store" };

export async function GET() {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401, headers: PRIVATE });
  }
  return NextResponse.json(list(downloadRunner()), { headers: PRIVATE });
}

export async function POST(request: Request) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401, headers: PRIVATE });
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid body" }, { status: 400, headers: PRIVATE });
  }
  const parsed = SubmitSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ error: "invalid body" }, { status: 400, headers: PRIVATE });
  }
  const out = await submit(downloadRunner(), check.personId, parsed.data.urls);
  return NextResponse.json(out.body, { status: out.status, headers: PRIVATE });
}
