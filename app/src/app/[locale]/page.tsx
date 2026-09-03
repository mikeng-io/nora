import { getTranslations } from "next-intl/server";
import { redirect } from "next/navigation";
import {
  Activity,
  Brain,
  Gamepad2,
  MapPin,
  Cloud,
  Bus,
  Hash,
  CalendarDays,
  CircleHelp,
  Compass,
  MessageCircle,
  Network,
  Users,
  Eye,
  Volume2,
} from "lucide-react";
import { signIn } from "@/auth";
import { checkSession } from "@/lib/require-session";
import { Button } from "@/components/ui/button";
import { LanguageSwitcher } from "@/components/language-switcher";
import { CapabilityTile } from "@/components/capability-tile";

// Landing capability tiles: same id -> messages / icon / variant convention
// as the login page's teaser tiles, widened to the full set Nora actually
// does (portal pages + chat skills). Static copy only — this is the public
// route, no API fetches; real data stays behind the auth wall. Icons are
// the brief's suggested 1:1s (ink + Gamepad2 etc. all already used in-app).
const LANDING_CAPABILITIES = [
  { id: "status", icon: Activity },
  { id: "memory", icon: Brain },
  { id: "games", icon: Gamepad2 },
  { id: "map", icon: MapPin },
  { id: "weather", icon: Cloud },
  { id: "transit", icon: Bus },
  { id: "roomWords", icon: Hash },
  { id: "events", icon: CalendarDays },
] as const;

// The architecture section's five storage tiers, in the canonical order of
// design/dreamer-agent-loop/19-between-tiers.md's tier table — one row per
// kind of knowing. Same id -> messages / icon convention as the capability
// tiles above; the badge names what the tier is ABOUT (people / her position
// / open questions / the world / words) rather than a status. Static copy
// only, like the rest of the public route.
const LANDING_TIERS = [
  { id: "memory", icon: Brain },
  { id: "standing", icon: Compass },
  { id: "notes", icon: CircleHelp },
  { id: "knowledge", icon: Network },
  { id: "language", icon: MessageCircle },
] as const;

// The positioning section — why Nora isn't another workspace agent. Three
// cards, one per competitor category, each carrying the single sharpest
// contrast from the positioning discussion (social memory / zero-LLM
// ambient / restraint-as-product). Icons: Users (people), Eye (ambient
// watching), Volume2 (silence).
const LANDING_DIFFS = [
  { id: "1", icon: Users },
  { id: "2", icon: Eye },
  { id: "3", icon: Volume2 },
] as const;

// The CTA is the only auth touchpoint and it reuses the login page's exact
// flow — a signIn("discord") server action with the same redirectTo. The
// signed-in allowed-user experience is untouched ((portal) layout still
// gates every downstream page; /login is unchanged).
export default async function LandingPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  const t = await getTranslations("landing");

  // Already signed in and allowlisted? Go straight to the portal — the
  // landing CTA must not send an authed user back through Discord OAuth.
  const check = await checkSession();
  if (check.status === "ok") {
    redirect(`/${locale}/status`);
  }

  return (
    <main className="relative min-h-svh overflow-hidden bg-bg-base">
      {/* Same backdrop as the login card: two drifting aurora blobs + the
          dot grid, all token-based and reduced-motion-safe. */}
      <div
        aria-hidden
        className="aurora-blob pointer-events-none absolute left-1/4 top-1/4 h-[520px] w-[520px] animate-aurora-drift-1 rounded-full blur-[110px]"
        style={{ background: "color-mix(in srgb, var(--accent) 30%, transparent)" }}
      />
      <div
        aria-hidden
        className="aurora-blob pointer-events-none absolute bottom-1/4 right-1/4 h-[480px] w-[480px] animate-aurora-drift-2 rounded-full blur-[110px]"
        style={{ background: "color-mix(in srgb, var(--accent-dim) 26%, transparent)" }}
      />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle,var(--border)_1px,transparent_1px)] bg-[length:28px_28px] opacity-[0.35]" />

      <div className="absolute right-4 top-4 z-20">
        <LanguageSwitcher />
      </div>

      <div className="relative z-10 mx-auto w-full max-w-7xl px-4 sm:px-6 md:px-10">
        {/* HERO — desktop: two-column split, vertically centred, full viewport.
            Left is the introduction; right is the sign-in card. Mobile stacks. */}
        <section className="grid min-h-svh grid-cols-1 items-center gap-10 py-16 lg:grid-cols-[1fr_400px] lg:gap-16">
          <div className="animate-fade-in-up">
            <p className="mb-4 font-mono text-[10px] uppercase tracking-wider text-text-muted">
              {t("heroEyebrow")}
            </p>
            <h1 className="mb-3 font-display text-5xl font-semibold uppercase tracking-tight md:text-6xl">
              {t("heroTitle")}
            </h1>
            <p className="mb-2 text-lg font-medium text-text-primary md:text-xl">
              {t("heroPositioning")}
            </p>
            <p className="mb-8 max-w-xl text-[15px] leading-relaxed text-text-secondary">
              {t("heroTagline")}
            </p>
          </div>

          {/* Right column — the sign-in card, same border-glow treatment as
              the login page. On desktop it anchors the right side; on mobile
              it stacks under the intro. */}
          <div className="animate-fade-in-up border-glow relative w-full max-w-sm justify-self-center rounded-2xl p-px shadow-lg [animation-delay:100ms] lg:max-w-none lg:justify-self-end">
            <div className="rounded-[15px] bg-bg-surface p-8">
              <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
                {t("cardEyebrow")}
              </p>
              <h2 className="mb-2 text-2xl font-semibold tracking-tight">{t("cardHeading")}</h2>
              <p className="mb-7 text-[13.5px] leading-relaxed text-text-secondary">
                {t("cardDek")}
              </p>
              <form
                action={async () => {
                  "use server";
                  await signIn("discord", { redirectTo: `/${locale}/status` });
                }}
              >
                <Button type="submit" variant="discord" className="w-full">
                  {t("cardCta")}
                </Button>
              </form>
            </div>
          </div>
        </section>

        {/* 佢係乜 — desktop: heading in a narrow left column, body to the right. */}
        <section className="animate-fade-in-up border-t border-border/50 py-14 md:py-20 [animation-delay:150ms]">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-[220px_1fr] md:gap-10">
            <p className="font-mono text-[10px] uppercase tracking-wider text-text-muted md:pt-1">
              {t("whatHeading")}
            </p>
            <p className="max-w-2xl text-[15px] leading-relaxed text-text-secondary">
              {t("whatBody")}
            </p>
          </div>
        </section>

        {/* 佢做到啲乜 — desktop: 4-column capability grid. */}
        <section className="animate-fade-in-up border-t border-border/50 py-14 md:py-20 [animation-delay:200ms]">
          <p className="mb-6 font-mono text-[10px] uppercase tracking-wider text-text-muted">
            {t("capabilityHeading")}
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {LANDING_CAPABILITIES.map((c) => (
              <CapabilityTile
                key={c.id}
                icon={c.icon}
                title={t(`capability.${c.id}.title`)}
                description={t(`capability.${c.id}.description`)}
                variant="live"
                badgeLabel={t("capabilityBadge")}
              />
            ))}
          </div>
        </section>

        {/* 點樣記嘢 — desktop: heading left, body right. */}
        <section className="animate-fade-in-up border-t border-border/50 py-14 md:py-20 [animation-delay:250ms]">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-[220px_1fr] md:gap-10">
            <p className="font-mono text-[10px] uppercase tracking-wider text-text-muted md:pt-1">
              {t("howHeading")}
            </p>
            <p className="max-w-2xl text-[15px] leading-relaxed text-text-secondary">
              {t("howBody")}
            </p>
          </div>
        </section>

        {/* 技術架構 — desktop: 3-column tier cards. */}
        <section className="animate-fade-in-up border-t border-border/50 py-14 md:py-20 [animation-delay:300ms]">
          <p className="mb-2 font-mono text-[10px] uppercase tracking-wider text-text-muted">
            {t("techHeading")}
          </p>
          <p className="mb-6 max-w-2xl text-[15px] leading-relaxed text-text-secondary">
            {t("techIntro")}
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {LANDING_TIERS.map((tier) => (
              <CapabilityTile
                key={tier.id}
                icon={tier.icon}
                title={t(`tier.${tier.id}.title`)}
                description={t(`tier.${tier.id}.description`)}
                variant="designed"
                badgeLabel={t(`tier.${tier.id}.domain`)}
              />
            ))}
          </div>
        </section>

        {/* 同其他 agent 有咩分別 — desktop: 3-column positioning cards. */}
        <section className="animate-fade-in-up border-t border-border/50 py-14 md:py-20 [animation-delay:325ms]">
          <p className="mb-2 font-mono text-[10px] uppercase tracking-wider text-text-muted">
            {t("diffHeading")}
          </p>
          <p className="mb-6 max-w-2xl text-[15px] leading-relaxed text-text-secondary">
            {t("diffIntro")}
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {LANDING_DIFFS.map((d) => (
              <CapabilityTile
                key={d.id}
                icon={d.icon}
                title={t(`diff.${d.id}.title`)}
                description={t(`diff.${d.id}.description`)}
                variant="designed"
                badgeLabel={t(`diff.${d.id}.badge`)}
              />
            ))}
          </div>
        </section>

        {/* 登入之後 — desktop: centred closing CTA. */}
        <section className="animate-fade-in-up border-t border-border/50 py-14 text-center md:py-20 [animation-delay:350ms]">
          <p className="mb-3 font-mono text-[10px] uppercase tracking-wider text-text-muted">
            {t("afterHeading")}
          </p>
          <p className="mx-auto max-w-xl text-[15px] leading-relaxed text-text-secondary">
            {t("afterBody")}
          </p>
        </section>
      </div>
    </main>
  );
}
