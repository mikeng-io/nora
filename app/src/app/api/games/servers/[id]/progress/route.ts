import { requireApiSession } from "@/lib/require-api-session";
import { proxyGamesGet } from "@/lib/games-proxy";

// Local-save games only (e.g. Big Walk) — a container-backed game has no
// such route on Pulse's side; Nora's own /app/v1/.../progress answers 503
// for it, same as any other unsupported combination.
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const denied = await requireApiSession();
  if (denied) return denied;

  const { id } = await params;
  return proxyGamesGet(`/app/v1/games/servers/${encodeURIComponent(id)}/progress`, "");
}
