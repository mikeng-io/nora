"use server";

import { headers } from "next/headers";
import { AuthError } from "next-auth";
import { DEV_LOGIN_ENABLED, signIn, signOut } from "@/auth";
import { routing } from "@/i18n/routing";
import { CODE_PLATFORMS, type CodePlatform, SendThrottle, clientKey, requestCode } from "./otp";

const sendThrottle = new SendThrottle();

const safeLocale = (l: string) =>
  (routing.locales as readonly string[]).includes(l) ? l : routing.defaultLocale;

/** Only a same-app path survives as a return address: never another origin. */
function safeNext(locale: string, next: string | null | undefined) {
  if (next && /^\/(zh-Hant|en)\/[A-Za-z0-9/_\-?=&.%]*$/.test(next) && !next.includes("//")) return next;
  return `/${locale}/today`;
}

export async function signInWithDiscord(locale: string, next?: string | null, fresh = false) {
  const l = safeLocale(locale);
  // `fresh`: after a refusal, ask Discord to show its consent screen again,
  // so the person can see which account they are about to use.
  await signIn("discord", { redirectTo: safeNext(l, next) }, fresh ? { prompt: "consent" } : undefined);
}

export async function signInAsDev(locale: string, discordId: string, next?: string | null) {
  if (!DEV_LOGIN_ENABLED) throw new Error("development sign-in is off");
  const l = safeLocale(locale);
  await signIn("dev", { discordId, redirectTo: safeNext(l, next) });
}

export async function signOutAction(locale: string) {
  await signOut({ redirectTo: `/${safeLocale(locale)}/login?signedOut=1` });
}

/** Where the code sign-in is (DEC-287). `ask` takes a platform and a number;
 * `code` takes the six digits Nora sent. `error` names what to say, never
 * whether the number belongs to anybody: Nora answers that the same way. */
export type CodeState = {
  step: "ask" | "code";
  platform: CodePlatform;
  identifier: string;
  challengeId?: string;
  error?: "unavailable" | "invalid" | "wrong" | "slow";
};

/** One action for both steps, chosen by the submit button's `intent`. */
export async function codeSignInAction(prev: CodeState, form: FormData): Promise<CodeState> {
  const intent = String(form.get("intent") ?? "");
  const locale = safeLocale(String(form.get("locale") ?? ""));
  if (intent === "back") return { step: "ask", platform: prev.platform, identifier: prev.identifier };

  if (intent === "send") {
    const raw = String(form.get("platform") ?? "");
    // `prev` comes back from the browser, so nothing in it is trusted as a choice.
    const platform: CodePlatform = (CODE_PLATFORMS as readonly string[]).includes(raw)
      ? (raw as CodePlatform)
      : "signal";
    const identifier = String(form.get("identifier") ?? "").trim().slice(0, 64);
    if (!identifier) return { step: "ask", platform, identifier, error: "invalid" };
    if (!sendThrottle.allow(clientKey(await headers()), Date.now())) {
      return { step: "ask", platform, identifier, error: "slow" };
    }
    const challengeId = await requestCode({ identifier, platform });
    if (!challengeId) return { step: "ask", platform, identifier, error: "unavailable" };
    return { step: "code", platform, identifier, challengeId };
  }

  // intent === "verify"
  const code = String(form.get("code") ?? "").replace(/\s/g, "");
  if (!prev.challengeId || !/^[0-9]{6}$/.test(code)) return { ...prev, error: "invalid" };
  try {
    await signIn("otp", {
      challengeId: prev.challengeId,
      code,
      redirectTo: safeNext(locale, String(form.get("next") ?? "") || null),
    });
  } catch (error) {
    // A refused code is an AuthError; a successful sign-in throws Next's
    // redirect, which must keep travelling.
    if (error instanceof AuthError) return { ...prev, error: "wrong" };
    throw error;
  }
  return prev;
}
