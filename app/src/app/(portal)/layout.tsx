import Link from "next/link";
import { redirect } from "next/navigation";
import fs from "node:fs";
import * as Separator from "@radix-ui/react-separator";
import { cva } from "class-variance-authority";
import { Activity, Sparkles } from "lucide-react";
import { auth } from "@/auth";
import { resolveDiscordPerson } from "@/lib/allowlist";
import { MODULES } from "@/lib/modules";
import { cn } from "@/lib/utils";

const ICONS: Record<string, typeof Activity> = {
  status: Activity,
  memory: Sparkles,
};

const navItem = cva(
  "flex items-center gap-2 rounded-md px-2.5 py-2 text-sm transition-colors text-[var(--text-secondary)] hover:bg-[var(--bg-elevated)] hover:text-[var(--text-primary)]",
);

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

  return (
    <div className="grid min-h-screen grid-cols-[240px_1fr]">
      <aside className="flex flex-col gap-7 border-r border-[var(--border)] bg-[var(--bg-surface)] p-3.5">
        <div className="flex items-center gap-2 border-b border-[var(--border)] px-2 pb-3">
          <div className="grid h-[22px] w-[22px] place-items-center border border-[var(--text-primary)] font-mono text-[11px] font-semibold">
            N
          </div>
          <span className="text-[15px] font-semibold tracking-tight">Nora</span>
        </div>

        <nav className="flex flex-col gap-0.5">
          {MODULES.map((m) => {
            const Icon = ICONS[m.id];
            return (
              <Link key={m.id} href={m.href} className={cn(navItem())}>
                <Icon className="h-3.5 w-3.5" />
                {m.label}
              </Link>
            );
          })}
        </nav>

        <Separator.Root className="h-px bg-[var(--border)]" />
      </aside>

      <main className="max-w-[880px] p-10">{children}</main>
    </div>
  );
}
