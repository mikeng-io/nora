import "server-only";
import fs from "node:fs";
import { auth } from "@/auth";
import { personById, resolveDiscordPerson, type Person } from "./allowlist";

// identity.toml is bind-mounted read-only into this container
// (docker-compose.yml's `app` service): the SAME bytes Nora's own process
// reads, so the portal's allowlist can never drift from her roster.
export const IDENTITY_TOML_PATH = process.env.IDENTITY_TOML_PATH ?? "/app/identity.toml";

export type SessionCheck =
  | ({ status: "ok"; image: string | null; via: "discord" | "code"; sessionId?: string } & Person)
  | { status: "no-session" }
  | { status: "denied"; name: string | null; image: string | null; via: "discord" | "code" };

let cached: { mtimeMs: number; text: string } | null = null;

/** Re-read only when the file changes: the check runs on every page and
 * every API read, and a person added to the roster should get in without
 * a restart. */
export function readIdentityToml(): string {
  // A runtime path (a bind mount), not a project file: the ignore comments
  // keep the build from tracing the whole project into the server bundle.
  const { mtimeMs } = fs.statSync(/*turbopackIgnore: true*/ IDENTITY_TOML_PATH);
  if (!cached || cached.mtimeMs !== mtimeMs) {
    cached = { mtimeMs, text: fs.readFileSync(/*turbopackIgnore: true*/ IDENTITY_TOML_PATH, "utf-8") };
  }
  return cached.text;
}

// The ONE place session + allowlist logic lives. The (portal) layout (the
// page gate) and every /api/v1 read call it: a page gate alone never
// protected the routes the pages fetch from (the original portal shipped
// GET /api/memory open to any request until a review caught it).
//
// Every sign-in ends as ONE identity.toml person: Discord through the
// account's `user_id`, a one-time code through the person_id Nora verified it
// for (DEC-287). Both are re-read against the file on every check, so somebody
// removed from it is out on their next request, whichever way they came in.
export async function checkSession(): Promise<SessionCheck> {
  const session = await auth();
  const user = session?.user;
  const image = user?.image ?? null;
  if (user?.personId) {
    const person = personById(readIdentityToml(), user.personId);
    return person
      ? { status: "ok", image, via: "code", sessionId: user.sessionId, ...person }
      : { status: "denied", name: null, image, via: "code" };
  }
  const discordId = user?.discordId;
  if (!discordId) return { status: "no-session" };
  const person = resolveDiscordPerson(readIdentityToml(), discordId);
  if (!person) return { status: "denied", name: user?.name ?? null, image, via: "discord" };
  return { status: "ok", image, via: "discord", sessionId: user?.sessionId, ...person };
}
