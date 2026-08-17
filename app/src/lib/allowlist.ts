import { parse } from "smol-toml";

/**
 * identity.toml's shape (design/10-identity-and-policy-schema.md):
 *
 *   [persons.miles]
 *   name = "Miles"
 *   [[persons.miles.discord]]
 *   user_id = "100000000000000003"
 *
 * TOML parses this as { persons: { miles: { name, discord: [{ user_id }] } } }.
 * `user_id` is the ONLY discord field ever matched on — `username` is
 * accepted in the schema for readability but never indexed, because it's
 * renameable (design/10 §3.1's "username is accepted and never matched"
 * rule). This mirrors that on the frontend rather than trusting every
 * caller to remember it.
 */
interface IdentityToml {
  persons?: Record<
    string,
    {
      name?: string;
      discord?: Array<{ user_id?: string }>;
    }
  >;
}

export function resolveDiscordPerson(
  identityToml: string,
  discordUserId: string,
): { personId: string; displayName: string } | null {
  const parsed = parse(identityToml) as IdentityToml;
  const persons = parsed.persons ?? {};

  for (const [personId, person] of Object.entries(persons)) {
    const discordBlocks = person.discord ?? [];
    const matches = discordBlocks.some((block) => block.user_id === discordUserId);
    if (matches) {
      return { personId, displayName: person.name ?? personId };
    }
  }
  return null;
}
