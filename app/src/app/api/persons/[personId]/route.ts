import { requireApiSession } from "@/lib/require-api-session";
import { proxyGamesGet } from "@/lib/games-proxy";

export async function GET(
  _request: Request, { params }: { params: Promise<{ personId: string }> },
) {
  const denied = await requireApiSession();
  if (denied) return denied;

  const { personId } = await params;
  return proxyGamesGet(`/app/v1/persons/${encodeURIComponent(personId)}`, "");
}
