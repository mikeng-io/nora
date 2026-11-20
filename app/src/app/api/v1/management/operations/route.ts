import { auth } from "@/auth";
import { identityBackend } from "@/server/management-session";
import { submitManagement } from "@/server/management-operation";

export async function POST(request: Request) {
  const headers = { "Cache-Control": "private, no-store" };
  try {
    const origin = new URL(process.env.AUTH_URL ?? request.url).origin;
    if (request.headers.get("Origin") !== origin) {
      return Response.json({ status: "denied" }, { status: 403, headers });
    }
    if (identityBackend() !== "managed") {
      return Response.json({ status: "denied" }, { status: 403, headers });
    }
    const session = await auth();
    if (!session?.user?.managedIdentity) {
      return Response.json({ status: "denied" }, { status: 401, headers });
    }
    return await submitManagement(session.user.managedIdentity, await request.text());
  } catch {
    return Response.json({ status: "unavailable" }, { status: 503, headers });
  }
}
