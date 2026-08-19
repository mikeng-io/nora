import { requireApiSession } from "@/lib/require-api-session";
import { proxyEventsGet } from "@/lib/events-proxy";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const denied = await requireApiSession();
  if (denied) return denied;

  const { id } = await params;
  return proxyEventsGet(`/app/v1/events/${encodeURIComponent(id)}`, "");
}
