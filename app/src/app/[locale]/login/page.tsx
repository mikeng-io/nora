import { getTranslations } from "next-intl/server";
import { signIn } from "@/auth";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";

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
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-bg-base p-8">
      <div className="animate-fade-in-up flex flex-col items-center gap-1">
        <h1 className="text-xl font-semibold tracking-tight">{t("heading")}</h1>
      </div>

      {denied && (
        <Alert variant="destructive" className="max-w-xs text-center">
          {t("denied")}
        </Alert>
      )}

      <form
        action={async () => {
          "use server";
          await signIn("discord", { redirectTo: `/${locale}/status` });
        }}
        className="animate-fade-in-up [animation-delay:120ms]"
      >
        <Button type="submit" variant="discord">
          {t("discord")}
        </Button>
      </form>
    </main>
  );
}
