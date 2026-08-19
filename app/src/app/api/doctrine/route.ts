import { requireApiSession } from "@/lib/require-api-session";
import { proxyGamesGet } from "@/lib/games-proxy";

export async function GET() {
  const denied = await requireApiSession();
  if (denied) return denied;

  return proxyGamesGet("/app/v1/doctrine", "");
}
