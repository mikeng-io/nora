import { checkSession, sessionFailure } from "@/server/session";
import { proxyDocumentRestore } from "@/server/document-restore";

// Restore an old revision of a document as a new one (design/artifact/lifecycle.md). The rules
// live in src/server/document-restore.ts.
const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

export async function POST(request: Request, { params }: { params: Promise<{ id: string; n: string }> }) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return sessionFailure(check);
  }
  const { id, n } = await params;
  return proxyDocumentRestore({ base: NORA_API_BASE, personId: check.personId, id, n, request });
}
