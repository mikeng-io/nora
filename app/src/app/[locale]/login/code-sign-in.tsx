"use client";

import { useActionState, useState } from "react";
import { useTranslations } from "next-intl";
import {
  Check,
  ChevronDown,
  CircleAlert,
  MessageCircle,
  MessageSquareLock,
  type LucideIcon,
} from "lucide-react";
import { NoraAvatar } from "@/components/ui/avatar";
import { cn } from "@/lib/cn";
import { DEFAULT_REGION, DIAL_REGIONS, flag, safeRegion } from "@/lib/dial-codes";
import { codeSignInAction, type CodeState } from "@/server/actions";
import type { CodePlatform } from "@/server/otp";
import { SubmitButton } from "./submit-button";

const FIELD =
  "h-11 w-full rounded-control border border-hairline bg-surface px-3 text-body text-ink " +
  "outline-none placeholder:text-ink-muted focus:border-seam focus:ring-2 focus:ring-denim lg:h-11";

// One glyph per messenger, drawn in ink like every other icon: the One-Accent
// Rule (DESIGN.md §4) leaves no room for two more brand colours.
const GLYPH: Record<CodePlatform, LucideIcon> = {
  signal: MessageSquareLock,
  whatsapp: MessageCircle,
};

/** Two steps, shown as two labels joined by one hairline (the Cable Rule):
 * where the visitor is, in denim; the other, muted. */
function Steps({ step, labels }: { step: CodeState["step"]; labels: [string, string] }) {
  return (
    <ol className="flex items-center gap-2 text-meta font-medium">
      {labels.map((label, i) => {
        const current = (i === 0) === (step === "ask");
        const done = i === 0 && step === "code";
        return (
          <li key={label} className="contents">
            {i === 1 ? <span aria-hidden="true" className="h-px flex-1 bg-hairline" /> : null}
            <span
              aria-current={current ? "step" : undefined}
              className={cn("flex items-center gap-1.5", current ? "text-denim" : "text-ink-muted")}
            >
              <span
                aria-hidden="true"
                className={cn(
                  "grid size-5 place-items-center rounded-full border text-[0.6875rem] font-semibold",
                  current ? "border-denim bg-denim text-surface" : "border-hairline",
                )}
              >
                {done ? <Check className="size-3" /> : i + 1}
              </span>
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

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
    region: DEFAULT_REGION,
    identifier: "",
  });
  const [platform, setPlatform] = useState<CodePlatform>(state.platform);
  const [region, setRegion] = useState(state.region);
  const platformName = (p: CodePlatform) => t(p === "signal" ? "signal" : "whatsapp");
  // Names from the message files, not `Intl.DisplayNames`: the server's ICU
  // and the browser's name Hong Kong differently, and the page then fails to
  // hydrate.
  const regionName = (code: string) => t(`regions.${code}`);
  const chosen = safeRegion(region);
  const SentGlyph = GLYPH[state.platform];

  return (
    <section aria-labelledby="code-title" className="grid gap-4">
      <div className="flex items-center gap-3 text-meta text-ink-muted before:h-px before:flex-1 before:bg-hairline after:h-px after:flex-1 after:bg-hairline">
        {t("or")}
      </div>

      <div className="grid gap-5 rounded-panel border border-hairline bg-surface p-5 sm:p-6">
        <header className="flex items-start gap-3">
          <NoraAvatar size={40} />
          <div className="grid gap-0.5">
            <h2 id="code-title" className="text-title font-semibold text-ink">
              {t("codeTitle")}
            </h2>
            <p className="text-meta text-ink-muted">{t("codeLead")}</p>
          </div>
        </header>

        <Steps step={state.step} labels={[t("stepNumber"), t("stepCode")]} />

        <form action={action} className="grid gap-4">
          <input type="hidden" name="locale" value={locale} />
          <input type="hidden" name="next" value={next ?? ""} />

          {state.step === "ask" ? (
            <>
              <fieldset className="grid gap-2">
                <legend className="mb-1 text-label font-medium text-ink-soft">{t("platform")}</legend>
                <div className="grid grid-cols-2 gap-2">
                  {(["signal", "whatsapp"] as const).map((p) => {
                    const Glyph = GLYPH[p];
                    const on = platform === p;
                    return (
                      <label
                        key={p}
                        className={cn(
                          "relative flex h-12 cursor-pointer items-center justify-center gap-2 rounded-control border text-body font-semibold",
                          "transition-[background-color,border-color,color] duration-100 ease-nora",
                          "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-denim",
                          on
                            ? "border-denim bg-hoodie text-ink ring-1 ring-denim"
                            : "border-hairline text-ink-soft hover:bg-hoodie",
                        )}
                      >
                        <input
                          type="radio"
                          name="platform"
                          value={p}
                          checked={on}
                          onChange={() => setPlatform(p)}
                          className="sr-only"
                        />
                        <Glyph className={cn("size-[1.125rem]", on ? "text-denim" : "")} aria-hidden="true" />
                        {platformName(p)}
                        {on ? (
                          <Check
                            className="absolute top-1.5 right-1.5 size-3.5 text-denim"
                            aria-hidden="true"
                          />
                        ) : null}
                      </label>
                    );
                  })}
                </div>
              </fieldset>

              <div className="grid gap-1.5">
                <label htmlFor="code-identifier" className="text-label font-medium text-ink-soft">
                  {t("phoneLabel")}
                </label>
                <div className="flex gap-2">
                  {/* The select is the real control, transparent over a short
                      face: closed, it shows only the flag and the code, open,
                      the full country names. */}
                  <div className="relative shrink-0 rounded-control has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-denim">
                    <span
                      aria-hidden="true"
                      className="flex h-11 items-center gap-1.5 rounded-control border border-hairline bg-surface pr-2 pl-3 text-body text-ink"
                    >
                      <span className="text-base leading-none">{flag(chosen.region)}</span>
                      <span className="tabular-nums">+{chosen.dial}</span>
                      <ChevronDown className="size-4 text-ink-muted" />
                    </span>
                    <select
                      name="region"
                      aria-label={t("country")}
                      value={chosen.region}
                      onChange={(e) => setRegion(e.target.value)}
                      className="absolute inset-0 cursor-pointer appearance-none opacity-0"
                    >
                      {DIAL_REGIONS.map((r) => (
                        <option key={r.region} value={r.region}>
                          {`${flag(r.region)} ${regionName(r.region)} +${r.dial}`}
                        </option>
                      ))}
                    </select>
                  </div>
                  <input
                    id="code-identifier"
                    name="identifier"
                    aria-describedby={state.error ? "code-hint code-error" : "code-hint"}
                    aria-invalid={state.error === "invalid" || undefined}
                    type="tel"
                    inputMode="tel"
                    autoComplete="tel-national"
                    required
                    maxLength={64}
                    defaultValue={state.identifier}
                    placeholder={t("phonePlaceholder")}
                    className={cn(FIELD, "min-w-0 tabular-nums")}
                  />
                </div>
                <span id="code-hint" className="text-meta text-ink-muted">
                  {t("phoneHint")}
                </span>
              </div>

              <SubmitButton variant="primary" pendingLabel={t("sending")} name="intent" value="send">
                {t("sendCode")}
              </SubmitButton>
            </>
          ) : (
            <>
              <p
                role="status"
                className="flex items-start gap-2.5 rounded-control bg-hoodie px-3 py-2.5 text-body text-ink-soft"
              >
                <SentGlyph className="mt-0.5 size-[1.125rem] shrink-0 text-denim" aria-hidden="true" />
                <span>
                  {t("codeSent", {
                    identifier: state.sentTo ?? state.identifier,
                    platform: platformName(state.platform),
                  })}
                </span>
              </p>
              <label className="grid gap-1.5">
                <span className="text-label font-medium text-ink-soft">{t("codeLabel")}</span>
                <input
                  name="code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  // A pasted "123 456" must fit; the server drops the space.
                  pattern="[0-9 ]{6,7}"
                  maxLength={7}
                  aria-describedby={state.error ? "code-error" : undefined}
                  aria-invalid={state.error === "wrong" || state.error === "invalid" || undefined}
                  required
                  autoFocus
                  placeholder="000000"
                  className={cn(
                    FIELD,
                    "h-14 text-center font-mono text-[1.5rem] tracking-[0.45em] placeholder:text-seam lg:h-14",
                  )}
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
                className="w-fit justify-self-center text-meta font-semibold text-denim underline-offset-4 hover:underline"
              >
                {t("back")}
              </button>
            </>
          )}

          {state.error ? (
            <p id="code-error" role="alert" className="flex items-center gap-2 text-body text-danger">
              <CircleAlert className="size-4 shrink-0" aria-hidden="true" />
              {t(
                state.error === "wrong"
                  ? "codeWrong"
                  : state.error === "invalid"
                    ? "codeInvalid"
                    : state.error === "slow"
                      ? "codeSlow"
                      : "codeUnavailable",
              )}
            </p>
          ) : null}
        </form>
      </div>
    </section>
  );
}
