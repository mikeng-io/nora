import { z } from "zod";
import { ApiError, apiPath } from "./client";

// The holder's own managed-identity surface (src/server/holder-links.ts and
// src/server/link-consent.ts). These schemas are the ONE shape shared by the
// portal's routes and its pages — the server modules re-export them so the
// consent unit and the UI bind the same server-read request. Everything here
// is a result of a check: a refused or half-done operation is DATA, not an
// exception, and the views render each status in its own words (R-27).

const account = z.object({
  platform: z.enum(["discord", "signal", "telegram"]),
  kind: z.string().min(1),
  value: z.string().min(1),
}).strict();
export type HolderAccount = z.infer<typeof account>;

/** The private request view shape — shared with the consent unit, which must
 * bind the same server-read request rather than re-parse it differently. */
export const linkRequestView = z.object({
  request_id: z.string().min(1), side: z.enum(["initiating", "receiving"]),
  state: z.enum(["pending", "committing", "committed", "refused", "cancelled", "expired"]),
  pair: z.object({ initiating: account, receiving: account }).strict(),
  surviving_person: z.string().min(1), created_at: z.iso.datetime({ offset: true }),
  expires_at: z.iso.datetime({ offset: true }),
  approved: z.array(z.enum(["initiating", "receiving"])),
  challenge: z.object({ nonce: z.string().min(1), approve: z.string(), refuse: z.string() }).strict(),
}).strict();
export type LinkRequestView = z.infer<typeof linkRequestView>;

/** What the fresh-provider flow records on the session, then hands the request
 * page through a sealed cookie — the browser never writes a word of it. */
export const linkConsentOutcomeSchema = z.object({
  requestId: z.string().min(1),
  action: z.enum(["approve", "refuse", "cancel"]),
  operationId: z.string().min(1),
  outcome: z.enum(["complete", "denied", "unavailable", "incomplete"]),
  state: z.string().min(1).optional(),
  at: z.number().int().nonnegative(),
}).strict();
export type LinkConsentOutcome = z.infer<typeof linkConsentOutcomeSchema>;

const okView = z.object({ status: z.literal("ok"), request: linkRequestView }).strict();
const okUnlink = z.object({ status: z.literal("ok") }).strict();
const failure = z.object({ status: z.enum(["denied", "unavailable", "incomplete"]),
  detail: z.string().optional() }).strict();

export type HolderFailure = z.infer<typeof failure>;

/** The envelope the holder routes answer — ok data, or one of the three
 * refusals. A transport failure, an unreadable body, or a shape the route
 * never emits is "unavailable" (couldn't check), never "denied" and never ok. */
async function holderCall<O>(
  path: string,
  ok: z.ZodType<O>,
  init?: RequestInit,
): Promise<O | HolderFailure> {
  let res: Response;
  try {
    res = await fetch(apiPath(path), {
      cache: "no-store",
      credentials: "same-origin",
      headers: { accept: "application/json" },
      ...init,
    });
  } catch {
    return { status: "unavailable" };
  }
  const body: unknown = await res.json().catch(() => undefined);
  const refused = failure.safeParse(body);
  if (refused.success) return refused.data;
  const succeeded = ok.safeParse(body);
  if (res.status === 200 && succeeded.success) return succeeded.data;
  if (res.status === 401) throw new ApiError(`signed out: ${path}`, 401, "signed-out");
  return { status: "unavailable" };
}

export const fetchLinkRequest = (requestId: string, signal?: AbortSignal) =>
  holderCall(`/holder/link-requests/${encodeURIComponent(requestId)}`, okView,
    { signal });

export const createLinkRequest = (body: {
  request_id: string;
  receiving: HolderAccount;
  surviving_person: string;
}) =>
  holderCall("/holder/link-requests", okView, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

export const unlinkAccount = (body: {
  operation_id: string;
  expected_person: string;
}) =>
  holderCall("/holder/unlink", okUnlink, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

// ------------------------------------------------------------ the decision

export type LinkVerb = "approve" | "refuse" | "cancel";

/** Platform names are proper nouns — capitalized for a label, not
 * translated. */
export const platformLabel = (platform: string) =>
  platform.slice(0, 1).toUpperCase() + platform.slice(1);

/** The verbs the view's own side may take (`nora/people/app/holder_link.py`
 * `_decision_kind`): either side approves, only the initiator cancels, only
 * the receiver refuses — and only while the request still asks. Anything
 * else the UI must not even show. */
export function availableVerbs(view: LinkRequestView, now = Date.now()): LinkVerb[] {
  if (view.state !== "pending" || Date.parse(view.expires_at) <= now
    || view.approved.includes(view.side)) {
    return [];
  }
  return view.side === "initiating" ? ["approve", "cancel"] : ["approve", "refuse"];
}

/** How this side's decision reaches Nora: a fresh Discord/Telegram sign-in
 * proves the account (the consent endpoint then navigates it), while Signal's
 * consent is the private message itself — the challenge words the view
 * carries, never a provider redirect. */
export function decisionTransport(view: LinkRequestView): "provider" | "signal" {
  return view.pair[view.side].platform === "signal" ? "signal" : "provider";
}
