import { signIn } from "@/auth";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ denied?: string }>;
}) {
  const { denied } = await searchParams;
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-8">
      <h1 className="text-2xl font-semibold">Nora</h1>
      {denied && (
        <p className="text-sm text-red-600">
          That Discord account isn&apos;t on the list. Ask Miles to add it.
        </p>
      )}
      <form
        action={async () => {
          "use server";
          await signIn("discord", { redirectTo: "/status" });
        }}
      >
        <button
          type="submit"
          className="rounded-md bg-[#5865F2] px-4 py-2 text-white hover:bg-[#4752C4]"
        >
          Sign in with Discord
        </button>
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
          className="mt-4 flex flex-col items-center gap-2 border-t border-dashed border-neutral-300 pt-4"
        >
          <p className="text-xs text-neutral-500">dev stub — no real OAuth</p>
          <input type="hidden" name="discordId" value="100000000000000003" />
          <button
            type="submit"
            className="rounded-md border border-neutral-400 px-4 py-2 text-sm hover:bg-neutral-100"
          >
            Continue as Miles (stub)
          </button>
        </form>
      )}
    </main>
  );
}
