import { z } from "zod";

export const PersonSummarySchema = z.object({
  personId: z.string(),
  displayName: z.string(),
});
export type PersonSummary = z.infer<typeof PersonSummarySchema>;

export const PersonsRosterSchema = z.array(PersonSummarySchema);

export const GameProfileSchema = z.object({
  serverId: z.string(),
  displayName: z.string(),
  game: z.string().nullable(),
});
export type GameProfile = z.infer<typeof GameProfileSchema>;

export const PersonProfileSchema = z.object({
  personId: z.string(),
  displayName: z.string(),
  gameProfiles: z.array(GameProfileSchema),
});
export type PersonProfile = z.infer<typeof PersonProfileSchema>;

export async function fetchPersonsRoster(): Promise<PersonSummary[]> {
  const res = await fetch("/api/persons", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`persons roster fetch failed: ${res.status}`);
  }
  return PersonsRosterSchema.parse(await res.json());
}

// `undefined` for a 404 (an unrecognized personId) — distinct from a thrown
// error, so a page can render its own "not found" state instead of the
// generic fetch-failed one.
export async function fetchPersonProfile(personId: string): Promise<PersonProfile | undefined> {
  const res = await fetch(`/api/persons/${encodeURIComponent(personId)}`, { cache: "no-store" });
  if (res.status === 404) return undefined;
  if (!res.ok) {
    throw new Error(`person profile fetch failed: ${res.status}`);
  }
  return PersonProfileSchema.parse(await res.json());
}
