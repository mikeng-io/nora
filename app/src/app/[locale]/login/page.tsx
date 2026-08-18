import { getTranslations } from "next-intl/server";
import { signIn } from "@/auth";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { LanguageSwitcher } from "@/components/language-switcher";

export default async function LoginPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ denied?: string }>;
}) {
  const { locale } = await params;
  const { denied } = await searchParams;
  const t = await getTranslations("login");

  return (
    <main className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-bg-base p-6">
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

      <div className="absolute right-4 top-4">
        <LanguageSwitcher />
      </div>

      <div className="animate-fade-in-up border-glow relative w-full max-w-sm rounded-2xl p-px shadow-lg">
        <div className="rounded-[15px] bg-bg-surface p-8">
          <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
            {t("eyebrow")}
          </p>
          <h1 className="mb-2 text-2xl font-semibold tracking-tight">{t("heading")}</h1>
          <p className="mb-7 text-[13.5px] leading-relaxed text-text-secondary">{t("dek")}</p>

          {denied && (
            <Alert variant="destructive" className="mb-5">
              {t("denied")}
            </Alert>
          )}

          <form
            action={async () => {
              "use server";
              await signIn("discord", { redirectTo: `/${locale}/status` });
            }}
          >
            <Button type="submit" variant="discord" className="w-full">
              {t("discord")}
            </Button>
          </form>
        </div>
      </div>
    </main>
  );
}
