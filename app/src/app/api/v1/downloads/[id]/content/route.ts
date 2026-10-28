import { checkSession } from "@/server/session";
import { downloadRunner } from "@/server/downloads/runner";
import { serveContent } from "@/server/downloads/service";

// A finished download's bytes. More specific than the catch-all proxy, which
// answers JSON only; the rules (session, id shape, one-range support, sandboxed
// headers) live in src/server/downloads/service.ts.
export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const check = await checkSession();
  if (check.status !== "ok") {
    return new Response(JSON.stringify({ error: "unauthorized" }), {
      status: 401,
      headers: { "content-type": "application/json", "cache-control": "private, no-store" },
    });
  }
  const { id } = await params;
  const file = new URL(request.url).searchParams.get("file");
  return serveContent(downloadRunner(), id, request.headers.get("range"), file);
}
