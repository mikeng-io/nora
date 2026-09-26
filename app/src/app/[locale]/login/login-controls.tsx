"use client";

import { useTranslations } from "next-intl";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/lib/theme";
import { LANGS, useLanguageSwitch } from "@/components/shell/account";
import { Segmented } from "@/components/ui/tabs";

// The login page's lighting and language, the same two choices the site's
// header offers, stored under the same keys.
export function LoginControls() {
  const t = useTranslations("account");
  const { resolved, setChoice } = useTheme();
  const { current, switchTo } = useLanguageSwitch();
  return (
    <div className="ml-auto flex items-center gap-1">
      <Segmented
        size="sm"
        label={t("language")}
        value={current}
        onChange={switchTo}
        options={LANGS.map((l) => ({ value: l.locale, label: <span lang={l.locale}>{l.short}</span> }))}
      />
      <button
        type="button"
        onClick={() => setChoice(resolved === "dark" ? "light" : "dark")}
        aria-label={resolved === "dark" ? t("toStudio") : t("toNight")}
        className="grid size-11 place-items-center rounded-control text-ink-soft transition-colors hover:bg-hoodie hover:text-ink"
      >
        {resolved === "dark" ? <Sun className="size-[18px]" /> : <Moon className="size-[18px]" />}
      </button>
    </div>
  );
}
