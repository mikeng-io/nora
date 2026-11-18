import "server-only";
import { z } from "zod";
import { postOtp } from "./otp";
import { captureManagedSession, managedSessionSchema, type ManagedSession } from "./management-session";

/** The code service supplies the original binding; no person-only response is sufficient. */
export async function verifyManagedCode(
  { challengeId, code }: { challengeId: string; code: string },
): Promise<ManagedSession | null> {
  const response = await postOtp("/app/v1/otp/verify",
    { challengeId, code, purpose: "sign_in" }, fetch);
  if (!response || response.status !== 200) return null;
  const parsed = z.object({ personId: z.string(), managedIdentity: managedSessionSchema })
    .strict().safeParse(await response.json().catch(() => null));
  if (!parsed.success) return null;
  const identity = parsed.data.managedIdentity;
  if (identity.authentication.sessionId !== `otp:${challengeId}`
    || parsed.data.personId !== identity.binding.person_id
    || identity.authentication.proofIssuer !== "nora-otp"
    || !["signal_code", "whatsapp_code"].includes(identity.authentication.method)) return null;
  return captureManagedSession(identity.authentication, identity.binding);
}
