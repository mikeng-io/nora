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
          await signIn("discord");
        }}
      >
        <button
          type="submit"
          className="rounded-md bg-[#5865F2] px-4 py-2 text-white hover:bg-[#4752C4]"
        >
          Sign in with Discord
        </button>
      </form>
    </main>
  );
}
