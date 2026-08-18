import { requireApiSession } from "@/lib/require-api-session";
import { proxyGamesGet } from "@/lib/games-proxy";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const denied = await requireApiSession();
  if (denied) return denied;

  const { id } = await params;
  const search = new URL(request.url).search;
  return proxyGamesGet(`/app/v1/games/servers/${encodeURIComponent(id)}/players`, search);
}
