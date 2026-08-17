import { redirect } from "next/navigation";
import fs from "node:fs";
import * as Separator from "@radix-ui/react-separator";
import { auth } from "@/auth";
import { resolveDiscordPerson } from "@/lib/allowlist";
import { NavLinks } from "@/components/nav-links";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

// identity.toml is bind-mounted read-only into this container at deploy
// time (docker-compose.yml's `app` service) — the SAME file Nora's own
// process reads, so the web portal's whitelist can never drift from Nora's
// roster.
const IDENTITY_TOML_PATH = process.env.IDENTITY_TOML_PATH ?? "/app/identity.toml";

// The auth+allowlist gate lives HERE, not in middleware.ts, because a real
// build (not just the plan) showed Next.js 15.5.23 has no working Node.js
// middleware runtime — `experimental.nodeMiddleware` isn't a recognized key
// in this version's own config validator, and middleware defaults to the
// Edge runtime, where `node:fs` doesn't exist. A layout is an async Server
// Component and runs in Node.js by default, no experimental flag needed —
// simpler AND correct, not a workaround. `/login` sits outside the
// `(portal)` route group, so it's the one route this never touches.
export default async function PortalLayout({ children }: { children: React.ReactNode }) {
  const session = await auth();
  const discordId = session?.user?.discordId;
  if (!discordId) {
    redirect("/login");
  }

  const identityToml = fs.readFileSync(IDENTITY_TOML_PATH, "utf-8");
  const person = resolveDiscordPerson(identityToml, discordId);
  if (!person) {
    redirect("/login?denied=1");
  }

  const initials = person.displayName
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <div className="grid min-h-screen grid-cols-[240px_1fr] bg-bg-base">
      <aside className="flex flex-col gap-7 border-r border-border bg-bg-surface p-3.5">
        <div className="flex items-center gap-2 border-b border-border px-2 pb-3">
          <div className="grid h-[22px] w-[22px] place-items-center border border-text-primary font-mono text-[11px] font-semibold">
            N
          </div>
          <span className="text-[15px] font-semibold tracking-tight">Nora</span>
        </div>

        <NavLinks />

        <Separator.Root className="mt-auto h-px bg-border" />

        <div className="flex items-center gap-2 px-1">
          <Avatar>
            <AvatarFallback>{initials}</AvatarFallback>
          </Avatar>
          <span className="truncate text-[13px] text-text-secondary">{person.displayName}</span>
        </div>
      </aside>

      <main className="max-w-[880px] p-10">{children}</main>
    </div>
  );
}
