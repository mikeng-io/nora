"use server";

import { DEV_LOGIN_ENABLED, signIn, signOut } from "@/auth";
import { routing } from "@/i18n/routing";

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
