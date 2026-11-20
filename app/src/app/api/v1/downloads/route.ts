import { NextResponse } from "next/server";
import { checkSession, sessionFailure } from "@/server/session";
import { downloadRunner } from "@/server/downloads/runner";
import { SubmitSchema, list, submit } from "@/server/downloads/service";

// The circle's downloads (one shared library): submit a batch of URLs, list every
// job. These are LOCAL routes — they never touch Nora's :9100 API; the rules live
// in src/server/downloads/*.
const PRIVATE = { "cache-control": "private, no-store" };

export async function GET() {
  const check = await checkSession();
  if (check.status !== "ok") {
    return sessionFailure(check);
  }
  return NextResponse.json(await list(downloadRunner()), { headers: PRIVATE });
}

export async function POST(request: Request) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return sessionFailure(check);
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
