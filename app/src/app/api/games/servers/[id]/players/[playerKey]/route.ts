import { requireApiSession } from "@/lib/require-api-session";
import { proxyGamesGet } from "@/lib/games-proxy";

export async function GET(
  _request: Request, { params }: { params: Promise<{ id: string; playerKey: string }> },
) {
  const denied = await requireApiSession();
  if (denied) return denied;

  const { id, playerKey } = await params;
  // Defensive decode before re-encoding, same reasoning as the page
  // component's own params.playerKey handling — avoids double-encoding a
  // `:`-bearing segment if this Route Handler's params ever arrive
  // pre-encoded.
  return proxyGamesGet(
    `/app/v1/games/servers/${encodeURIComponent(id)}/players/${encodeURIComponent(decodeURIComponent(playerKey))}`,
    "",
  );
}
