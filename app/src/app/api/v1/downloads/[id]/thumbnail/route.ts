import { NextResponse } from "next/server";
import { checkSession } from "@/server/session";
import { downloadRunner } from "@/server/downloads/runner";
import { serveThumbnail } from "@/server/downloads/service";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  return serveThumbnail(downloadRunner(), (await params).id);
}
