import { z } from "zod";
import { apiGet } from "./client";

// nora/web/persons.py: identity.toml's roster, and one person's profile
// (their cross-platform identity).
export const PersonSummarySchema = z.object({
  personId: z.string(),
  displayName: z.string(),
});
export type PersonSummary = z.infer<typeof PersonSummarySchema>;
export const RosterSchema = z.array(PersonSummarySchema);

export const PersonProfileSchema = z.object({
  personId: z.string(),
  displayName: z.string(),
});
export type PersonProfile = z.infer<typeof PersonProfileSchema>;

export const fetchRoster = (signal?: AbortSignal) => apiGet("/persons", RosterSchema, { signal });
export const fetchPerson = (personId: string, signal?: AbortSignal) =>
  apiGet(`/persons/${encodeURIComponent(personId)}`, PersonProfileSchema, { signal });
