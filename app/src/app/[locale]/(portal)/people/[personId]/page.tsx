import { checkSession } from "@/lib/require-session";
import { PersonProfileClient } from "./person-profile-client";

// A Server Component (not "use client") for exactly one reason: knowing
// whether the viewer is looking at their OWN profile requires the same
// session read every other page gate uses (checkSession(), which touches
// node:fs and next-auth's server-only `auth()`) — a client component can't
// call it directly. The actual data fetching (loading/error state) still
// needs client-side hooks, so that part is delegated to a client child
// rather than making this whole page client and losing the session read.
export default async function PersonProfilePage(
  { params }: { params: Promise<{ personId: string }> },
) {
  const { personId } = await params;
  const check = await checkSession();
  const isOwnProfile = check.status === "ok" && check.personId === personId;

  return <PersonProfileClient personId={personId} isOwnProfile={isOwnProfile} />;
}
