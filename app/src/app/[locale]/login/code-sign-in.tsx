"use client";

import { useActionState, useState } from "react";
import { useTranslations } from "next-intl";
import { CircleAlert } from "lucide-react";
import { cn } from "@/lib/cn";
import { codeSignInAction, type CodeState } from "@/server/actions";
import type { CodePlatform } from "@/server/otp";
import { SubmitButton } from "./submit-button";

const FIELD =
  "h-11 w-full rounded-control border border-hairline bg-surface px-3 text-body text-ink " +
  "outline-none placeholder:text-ink-muted focus:border-seam focus:ring-2 focus:ring-denim lg:h-11";

/** Sign in with a code Nora sends to your own Signal or WhatsApp (DEC-287).
 * Two steps on one form: pick where and give your number, then type the six
 * digits. Nora answers the first step the same whether or not she knows the
 * number, so this never says "not on the list" — only that a code is on its
 * way if it is. */
export function CodeSignIn({ locale, next }: { locale: string; next?: string }) {
  const t = useTranslations("login");
  const [state, action] = useActionState<CodeState, FormData>(codeSignInAction, {
    step: "ask",
    platform: "signal",
    identifier: "",
  });
  const [platform, setPlatform] = useState<CodePlatform>(state.platform);
  const platformName = (p: CodePlatform) => t(p === "signal" ? "signal" : "whatsapp");

  return (
    <section aria-labelledby="code-title" className="grid gap-3">
      <div className="flex items-center gap-3 text-meta text-ink-muted before:h-px before:flex-1 before:bg-hairline after:h-px after:flex-1 after:bg-hairline">
        {t("or")}
      </div>
      <h2 id="code-title" className="text-title font-semibold text-ink">
        {t("codeTitle")}
      </h2>

      <form action={action} className="grid gap-3">
        <input type="hidden" name="locale" value={locale} />
        <input type="hidden" name="next" value={next ?? ""} />

        {state.step === "ask" ? (
          <>
            <fieldset className="grid gap-2">
              <legend className="mb-1 text-label font-medium text-ink-soft">{t("platform")}</legend>
              <div className="grid grid-cols-2 gap-2">
                {(["signal", "whatsapp"] as const).map((p) => (
                  <label
                    key={p}
                    className={cn(
                      "flex h-11 cursor-pointer items-center justify-center rounded-control border text-body font-semibold transition-colors",
                      platform === p
                        ? "border-denim bg-surface text-ink ring-1 ring-denim"
                        : "border-hairline text-ink-soft hover:bg-hoodie",
                    )}
                  >
                    <input
                      type="radio"
                      name="platform"
                      value={p}
                      checked={platform === p}
                      onChange={() => setPlatform(p)}
                      className="sr-only"
                    />
                    {platformName(p)}
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="grid gap-1.5">
              <span className="text-label font-medium text-ink-soft">{t("phoneLabel")}</span>
              <input
                name="identifier"
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                required
                maxLength={64}
                defaultValue={state.identifier}
                placeholder={t("phonePlaceholder")}
                className={FIELD}
              />
              <span className="text-meta text-ink-muted">{t("phoneHint")}</span>
            </label>
            <SubmitButton variant="outline" pendingLabel={t("sending")} name="intent" value="send">
              {t("sendCode")}
            </SubmitButton>
          </>
        ) : (
          <>
            <p role="status" className="text-body text-ink-soft">
              {t("codeSent", { identifier: state.identifier, platform: platformName(state.platform) })}
            </p>
            <label className="grid gap-1.5">
              <span className="text-label font-medium text-ink-soft">{t("codeLabel")}</span>
              <input
                name="code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                maxLength={6}
                required
                autoFocus
                placeholder="123456"
                className={cn(FIELD, "font-mono tracking-[0.3em]")}
              />
            </label>
            <SubmitButton variant="primary" pendingLabel={t("verifying")} name="intent" value="verify">
              {t("verify")}
            </SubmitButton>
            <button
              type="submit"
              name="intent"
              value="back"
              formNoValidate
              className="w-fit text-meta font-semibold text-denim underline-offset-4 hover:underline"
            >
              {t("back")}
            </button>
          </>
        )}

        {state.error ? (
          <p role="alert" className="flex items-center gap-2 text-body text-danger">
            <CircleAlert className="size-4 shrink-0" aria-hidden="true" />
            {t(
              state.error === "wrong"
                ? "codeWrong"
                : state.error === "invalid"
                  ? "codeInvalid"
                  : "codeUnavailable",
            )}
          </p>
        ) : null}
      </form>
    </section>
  );
}
