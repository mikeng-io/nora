import { checkSession, sessionFailure } from "@/server/session";
import { proxyDocumentMedia } from "@/server/document-media";

// A document's picture (ADR 0020). More specific than the catch-all proxy, which
// answers JSON only; the rules live in src/server/document-media.ts.
const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string; sha: string }> },
) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return sessionFailure(check);
  }
  const { id, sha } = await params;
  return proxyDocumentMedia({ base: NORA_API_BASE, personId: check.personId, id, sha });
}
