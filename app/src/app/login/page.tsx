import { signIn } from "@/auth";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ denied?: string }>;
}) {
  const { denied } = await searchParams;
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-bg-base p-8">
      <div className="flex flex-col items-center gap-1">
        <div className="grid h-9 w-9 place-items-center rounded-md border border-text-primary font-mono text-sm font-semibold">
          N
        </div>
        <h1 className="mt-2 text-xl font-semibold tracking-tight">Nora</h1>
      </div>

      {denied && (
        <Alert variant="destructive" className="max-w-xs text-center">
          That Discord account isn&apos;t on the list. Ask Miles to add it.
        </Alert>
      )}

      <form
        action={async () => {
          "use server";
          await signIn("discord", { redirectTo: "/status" });
        }}
      >
        <Button type="submit" variant="discord">
          Sign in with Discord
        </Button>
      </form>

      {process.env.AUTH_STUB === "true" && (
        <form
          action={async (formData) => {
            "use server";
            await signIn("stub", {
              discordId: formData.get("discordId"),
              redirectTo: "/status",
            });
          }}
          className="flex flex-col items-center gap-2 border-t border-dashed border-border-strong pt-5"
        >
          <p className="font-mono text-[10px] uppercase tracking-wider text-text-muted">
            dev stub — no real OAuth
          </p>
          <input type="hidden" name="discordId" value="100000000000000003" />
          <Button type="submit" variant="outline" size="sm">
            Continue as Miles (stub)
          </Button>
        </form>
      )}
    </main>
  );
}
