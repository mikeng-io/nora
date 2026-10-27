import { NextResponse } from "next/server";
import { checkSession } from "@/server/session";
import { downloadRunner } from "@/server/downloads/runner";
import { serveArchive } from "@/server/downloads/service";

const PRIVATE = { "cache-control": "private, no-store" };

export async function GET() {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401, headers: PRIVATE });
  }
  return serveArchive(downloadRunner());
}
