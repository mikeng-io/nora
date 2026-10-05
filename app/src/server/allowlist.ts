import { parse } from "smol-toml";

/**
 * identity.toml's shape (design/10-identity-and-policy-schema.md):
 *
 *   [persons.miles]
 *   name = "Miles"
 *   [[persons.miles.discord]]
 *   user_id = "100000000000000003"
 *
 * `user_id` is the ONLY discord field ever matched on. `username` is
 * accepted in the file for readability and never indexed, because it can be
 * renamed (design/10 §3.1).
 */
interface IdentityToml {
  persons?: Record<string, { name?: string; discord?: Array<{ user_id?: string }> }>;
}

export interface Person {
  personId: string;
  displayName: string;
}

export function resolveDiscordPerson(identityToml: string, discordUserId: string): Person | null {
  const persons = (parse(identityToml) as IdentityToml).persons ?? {};
  for (const [personId, person] of Object.entries(persons)) {
    if ((person.discord ?? []).some((block) => block.user_id === discordUserId)) {
      return { personId, displayName: person.name ?? personId };
    }
  }
  return null;
}

/** The person a one-time code was verified for (DEC-287), re-read from the
 * file on every check so somebody removed from identity.toml is out at once,
 * whichever way they signed in. */
export function personById(identityToml: string, personId: string): Person | null {
  const persons = (parse(identityToml) as IdentityToml).persons ?? {};
  // `hasOwn`, so an id like "constructor" is not found on the prototype.
  if (!Object.hasOwn(persons, personId)) return null;
  return { personId, displayName: persons[personId].name ?? personId };
}

/** Everyone in the file who can sign in (has a Discord block): the dev
 * sign-in's picker. */
export function discordPeople(identityToml: string): Array<Person & { discordId: string }> {
  const persons = (parse(identityToml) as IdentityToml).persons ?? {};
  return Object.entries(persons).flatMap(([personId, p]) => {
    const id = p.discord?.find((b) => b.user_id)?.user_id;
    return id ? [{ personId, displayName: p.name ?? personId, discordId: id }] : [];
  });
}
