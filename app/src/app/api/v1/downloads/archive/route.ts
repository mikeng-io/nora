import { checkSession, sessionFailure } from "@/server/session";
import { downloadRunner } from "@/server/downloads/runner";
import { serveArchive } from "@/server/downloads/service";

export async function GET() {
  const check = await checkSession();
  if (check.status !== "ok") {
    return sessionFailure(check);
  }
  return serveArchive(downloadRunner());
}
