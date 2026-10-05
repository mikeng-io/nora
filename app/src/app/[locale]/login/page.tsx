import type { Metadata } from "next";
import Image from "next/image";
import { redirect } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { ArrowLeft, CircleAlert } from "lucide-react";
import { DEV_LOGIN_ENABLED } from "@/auth";
import { art } from "@/components/nora/art";
import { NoraMark, Wordmark } from "@/components/ui/avatar";
import { discordPeople } from "@/server/allowlist";
import { checkSession, readIdentityToml } from "@/server/session";
import { signInAsDev, signInWithDiscord, signOutAction } from "@/server/actions";
import { codeSignInEnabled } from "@/server/otp";
import { CodeSignIn } from "./code-sign-in";
import { ForgetDevice } from "./forget-device";
import { LoginControls } from "./login-controls";
import { SubmitButton } from "./submit-button";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "login" });
  return { title: t("metaTitle") };
}

function DiscordGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="size-5" fill="currentColor">
      <path d="M20.32 4.37a19.8 19.8 0 0 0-4.89-1.52.07.07 0 0 0-.08.04c-.21.38-.44.87-.61 1.25a18.27 18.27 0 0 0-5.49 0 12.6 12.6 0 0 0-.62-1.25.08.08 0 0 0-.08-.04A19.74 19.74 0 0 0 3.68 4.37a.07.07 0 0 0-.03.03C.53 9.05-.32 13.58.1 18.06a.08.08 0 0 0 .03.06 19.9 19.9 0 0 0 5.99 3.03.08.08 0 0 0 .08-.03c.46-.63.87-1.3 1.23-1.99a.08.08 0 0 0-.04-.11 13.1 13.1 0 0 1-1.87-.89.08.08 0 0 1 0-.13l.37-.29a.07.07 0 0 1 .08-.01c3.93 1.79 8.18 1.79 12.06 0a.07.07 0 0 1 .08.01l.37.29a.08.08 0 0 1 0 .13c-.6.35-1.22.64-1.87.89a.08.08 0 0 0-.04.11c.36.7.77 1.36 1.22 1.99a.08.08 0 0 0 .08.03 19.84 19.84 0 0 0 6-3.03.08.08 0 0 0 .04-.06c.5-5.18-.84-9.67-3.55-13.66a.06.06 0 0 0-.03-.03ZM8.02 15.33c-1.18 0-2.16-1.08-2.16-2.42 0-1.33.96-2.42 2.16-2.42 1.21 0 2.18 1.1 2.16 2.42 0 1.34-.96 2.42-2.16 2.42Zm7.97 0c-1.18 0-2.15-1.08-2.15-2.42 0-1.33.95-2.42 2.15-2.42 1.21 0 2.18 1.1 2.16 2.42 0 1.34-.95 2.42-2.16 2.42Z" />
    </svg>
  );
}

export default async function LoginPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ denied?: string; signedOut?: string; next?: string; error?: string }>;
}) {
  const { locale } = await params;
  const { denied, signedOut, next, error } = await searchParams;
  const t = await getTranslations("login");

  // Signed in and on the list: straight in. The login page never sends an
  // allowed person back through Discord.
  const check = await checkSession();
  if (check.status === "ok") redirect(next?.startsWith(`/${locale}/`) ? next : `/${locale}/today`);
  const refused = check.status === "denied" || denied === "1";

  const devPeople = DEV_LOGIN_ENABLED ? discordPeople(readIdentityToml()) : [];
  const siteHome = locale === "en" ? "/en/" : "/";

  return (
    <div className="min-h-dvh bg-studio">
      <ForgetDevice />
      <div className="mx-auto grid min-h-dvh w-full max-w-[1180px] grid-rows-[auto_1fr_auto] px-4 sm:px-6 lg:px-10">
        <header className="safe-top flex h-16 items-center gap-2">
          <a href={siteHome} className="flex items-center gap-2 rounded-full pr-2" aria-label={t("toSite")}>
            <NoraMark size={30} />
            <Wordmark />
          </a>
          <LoginControls />
        </header>

        <main className="grid items-center gap-10 py-6 lg:grid-cols-[minmax(0,1fr)_420px] lg:gap-20 lg:py-10">
          <div className="page-enter mx-auto grid w-full max-w-[440px] gap-7 lg:mx-0">
            <Image
              src={art.wave}
              alt=""
              priority
              sizes="120px"
              className="h-auto w-24 lg:hidden"
            />
            <div className="grid gap-3">
              <p className="inline-flex w-fit items-center gap-2 rounded-full border border-hairline bg-surface py-1 pr-3.5 pl-3 text-meta font-medium text-ink-soft">
                <span className="size-2 rounded-full bg-blush" aria-hidden="true" />
                {t("status")}
              </p>
              <h1 className="text-[2rem] leading-[1.08] font-[650] tracking-[-0.03em] text-ink sm:text-[2.5rem] lang-zh:tracking-normal">
                {t("title")}
              </h1>
              <p className="text-lead text-ink-soft">
                {t(codeSignInEnabled() ? "ledeWithCode" : "lede")}
              </p>
            </div>

            {refused ? (
              <div
                role="alert"
                className="grid gap-3 rounded-panel border border-hairline bg-surface p-4"
              >
                <p className="flex items-start gap-2.5 text-body font-semibold text-ink">
                  <CircleAlert className="mt-0.5 size-5 shrink-0 text-danger" aria-hidden="true" />
                  <span>
                    {t("deniedTitle")}
                    <span className="mt-1 block font-normal text-ink-soft">
                      {check.status === "denied" && check.name
                        ? t("deniedAs", { name: check.name })
                        : t("deniedBody")}
                    </span>
                  </span>
                </p>
                <form
                  action={async () => {
                    "use server";
                    await signOutAction(locale);
                  }}
                >
                  <button
                    type="submit"
                    className="text-meta font-semibold text-denim underline-offset-4 hover:underline"
                  >
                    {t("forget")}
                  </button>
                </form>
              </div>
            ) : null}

            {signedOut === "1" && !refused ? (
              <p role="status" className="text-body text-ink-soft">
                {t("signedOut")}
              </p>
            ) : null}
            {error && !refused ? (
              <p role="alert" className="flex items-center gap-2 text-body text-danger">
                <CircleAlert className="size-4" aria-hidden="true" />
                {t("error")}
              </p>
            ) : null}

            <form
              action={async () => {
                "use server";
                await signInWithDiscord(locale, next, refused);
              }}
              className="grid gap-3"
            >
              <SubmitButton variant="discord" pendingLabel={t("redirecting")}>
                <DiscordGlyph />
                {refused ? t("tryAnother") : t("discord")}
              </SubmitButton>
              {codeSignInEnabled() ? null : (
                <p className="text-meta text-ink-muted">{t("footnote")}</p>
              )}
            </form>

            {codeSignInEnabled() ? (
              <>
                <CodeSignIn locale={locale} next={next} />
                <p className="text-meta text-ink-muted">{t("footnote")}</p>
              </>
            ) : null}

            {devPeople.length > 0 ? (
              <section className="grid gap-3 rounded-panel border border-dashed border-seam p-4">
                <div>
                  <h2 className="text-title font-semibold text-ink">{t("devTitle")}</h2>
                  <p className="text-meta text-ink-muted">{t("devBody")}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {devPeople.map((p) => (
                    <form
                      key={p.personId}
                      action={async () => {
                        "use server";
                        await signInAsDev(locale, p.discordId, next);
                      }}
                    >
                      <SubmitButton variant="outline" size="sm">
                        {p.displayName}
                      </SubmitButton>
                    </form>
                  ))}
                </div>
              </section>
            ) : null}
          </div>

          <figure className="hidden lg:grid lg:gap-3.5">
            <div className="relative aspect-[2/3] overflow-hidden rounded-sheet bg-hoodie shadow-[0_0_0_1px_var(--hairline)]">
              <Image
                src={art.canonical}
                alt={t("portraitAlt")}
                priority
                sizes="420px"
                className="size-full object-cover dark:hidden"
              />
              <Image
                src={art.nightPortrait}
                alt={t("portraitNightAlt")}
                sizes="420px"
                className="hidden size-full object-cover dark:block"
              />
            </div>
            <figcaption className="flex items-center gap-3 font-mono text-meta text-ink-muted before:h-px before:w-7 before:bg-seam">
              {t("caption")}
            </figcaption>
          </figure>
        </main>

        <footer className="safe-bottom flex items-center py-5">
          <a
            href={siteHome}
            className="inline-flex items-center gap-1.5 rounded-control py-2 pr-2 text-meta font-medium text-ink-muted transition-colors hover:text-ink"
          >
            <ArrowLeft className="size-4" aria-hidden="true" />
            {t("toSite")}
          </a>
        </footer>
      </div>
    </div>
  );
}
