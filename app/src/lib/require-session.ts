import fs from "node:fs";
import { auth } from "@/auth";
import { resolveDiscordPerson } from "@/lib/allowlist";

// identity.toml is bind-mounted read-only into this container at deploy
// time (docker-compose.yml's `app` service) — the SAME file Nora's own
// process reads, so the web portal's whitelist can never drift from Nora's
// roster.
const IDENTITY_TOML_PATH = process.env.IDENTITY_TOML_PATH ?? "/app/identity.toml";

export type SessionCheck =
  | { status: "ok"; personId: string; displayName: string }
  | { status: "no-session" }
  | { status: "denied" };

// The ONE place session + identity.toml allowlist logic lives — both
// (portal)/layout.tsx (the page gate) and every app/api/* proxy route call
// this, rather than each re-implementing the fs.readFileSync +
// resolveDiscordPerson check. Discovered missing entirely from the API
// routes by an automated commit review: without this, GET /api/memory
// returned every friend's full Honcho representation to ANY request, no
// Discord login required — the page gate never protected the routes the
// pages themselves fetch from.
export async function checkSession(): Promise<SessionCheck> {
  const session = await auth();
  const discordId = session?.user?.discordId;
  if (!discordId) {
    return { status: "no-session" };
  }
  const identityToml = fs.readFileSync(IDENTITY_TOML_PATH, "utf-8");
  const person = resolveDiscordPerson(identityToml, discordId);
  if (!person) {
    return { status: "denied" };
  }
  return { status: "ok", personId: person.personId, displayName: person.displayName };
}
