import { privateApiFetch } from "@/server/private-api-fetch";
import { checkSession, sessionFailure } from "@/server/session";
import { proxyArtifactDownload } from "@/server/artifact-download";

// One Artifacts file (ADR 0023). More specific than the catch-all proxy, which answers
// JSON only; the rules live in src/server/artifact-download.ts.
const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return sessionFailure(check);
  }
  const { id } = await params;
  return proxyArtifactDownload({
    fetchImpl: privateApiFetch,
    base: NORA_API_BASE,
    personId: check.personId,
    id,
    range: request.headers.get("range"),
  });
}
