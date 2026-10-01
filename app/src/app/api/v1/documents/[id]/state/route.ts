import { NextResponse } from "next/server";
import { checkSession } from "@/server/session";
import { proxyDocumentState } from "@/server/document-state";

// Archive, delete or restore a document (ADR 0020). The portal's one write; the rules
// live in src/server/document-state.ts.
const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json(
      { error: "unauthorized" },
      { status: 401, headers: { "cache-control": "private, no-store" } },
    );
  }
  const { id } = await params;
  return proxyDocumentState({ base: NORA_API_BASE, personId: check.personId, id, request });
}
