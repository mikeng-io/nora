"use client";

import * as Menu from "@radix-ui/react-dropdown-menu";
import { Drawer } from "vaul";
import { useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Check, ChevronsUpDown, Download, LogOut, Monitor, Moon, Share, Sun, UserRound } from "lucide-react";
import { Link } from "@/i18n/routing";
import { PersonAvatar } from "@/components/ui/avatar";
import { Segmented } from "@/components/ui/tabs";
import { useInstall } from "@/hooks/use-install";
import { useViewer } from "./viewer";
import { LANGS, useLanguageSwitch, useSignOut, useThemeLabels } from "./account";

const itemClass =
  "flex h-10 cursor-pointer items-center gap-2.5 rounded-control px-2.5 text-[0.9375rem] text-ink outline-none select-none " +
  "data-[highlighted]:bg-hoodie [&_svg]:size-4 [&_svg]:text-ink-muted";

const THEME_ICON = { light: Sun, dark: Moon, system: Monitor } as const;

/** Desktop: the viewer's chip at the foot of the rail opens this menu. */
export function AccountMenu() {
  const t = useTranslations("account");
  const viewer = useViewer();
  const { choice, setChoice, options } = useThemeLabels();
  const { current, switchTo } = useLanguageSwitch();
  const { state: installState, install } = useInstall();
  const { signOut, pending } = useSignOut();

  return (
    <Menu.Root>
      <Menu.Trigger
        className="flex w-full items-center gap-2.5 rounded-control p-2 text-left transition-colors duration-100 hover:bg-hoodie data-[state=open]:bg-hoodie"
        aria-label={t("open")}
      >
        <PersonAvatar id={viewer.personId} name={viewer.displayName} size={32} />
        <span className="grid min-w-0 flex-1">
          <span className="truncate text-[0.875rem] font-semibold text-ink">{viewer.displayName}</span>
          <span className="truncate text-label text-ink-muted">{t(viewer.via === "code" ? "signedInCode" : "signedIn")}</span>
        </span>
        <ChevronsUpDown className="size-4 shrink-0 text-ink-muted" aria-hidden="true" />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          side="top"
          align="start"
          sideOffset={8}
          className="z-50 w-[var(--radix-dropdown-menu-trigger-width)] min-w-64 rounded-panel border border-hairline bg-surface p-1.5 shadow-float animate-fade"
        >
          <Menu.Item asChild className={itemClass}>
            <Link href={`/people/${viewer.personId}`}>
              <UserRound aria-hidden="true" />
              {t("yourPage")}
            </Link>
          </Menu.Item>
          <Menu.Separator className="my-1.5 h-px bg-hairline" />
          <Menu.Label className="px-2.5 pt-1 pb-1.5 text-label font-medium text-ink-muted">
            {t("lighting")}
          </Menu.Label>
          <Menu.RadioGroup value={choice} onValueChange={(v) => setChoice(v as typeof choice)}>
            {options.map((o) => {
              const Icon = THEME_ICON[o.value];
              return (
                <Menu.RadioItem key={o.value} value={o.value} className={itemClass}>
                  <Icon aria-hidden="true" />
                  <span className="flex-1">{o.label}</span>
                  <Menu.ItemIndicator>
                    <Check className="!text-denim" aria-hidden="true" />
                  </Menu.ItemIndicator>
                </Menu.RadioItem>
              );
            })}
          </Menu.RadioGroup>
          <Menu.Separator className="my-1.5 h-px bg-hairline" />
          <Menu.Label className="px-2.5 pt-1 pb-1.5 text-label font-medium text-ink-muted">
            {t("language")}
          </Menu.Label>
          <Menu.RadioGroup value={current} onValueChange={(v) => switchTo(v as typeof current)}>
            {LANGS.map((l) => (
              <Menu.RadioItem key={l.locale} value={l.locale} className={itemClass} lang={l.locale}>
                <span className="w-4 text-center font-mono text-label text-ink-muted" aria-hidden="true">
                  {l.short}
                </span>
                <span className="flex-1">{l.name}</span>
                <Menu.ItemIndicator>
                  <Check className="!text-denim" aria-hidden="true" />
                </Menu.ItemIndicator>
              </Menu.RadioItem>
            ))}
          </Menu.RadioGroup>
          <Menu.Separator className="my-1.5 h-px bg-hairline" />
          {installState === "prompt" ? (
            <Menu.Item className={itemClass} onSelect={() => void install()}>
              <Download aria-hidden="true" />
              {t("install")}
            </Menu.Item>
          ) : null}
          <Menu.Item className={itemClass} disabled={pending} onSelect={signOut}>
            <LogOut aria-hidden="true" />
            {t("signOut")}
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

/** Phone: the avatar in the top bar opens this sheet. */
export function AccountSheet({ trigger }: { trigger: ReactNode }) {
  const t = useTranslations("account");
  const viewer = useViewer();
  const [open, setOpen] = useState(false);
  const { choice, setChoice, options } = useThemeLabels();
  const { current, switchTo } = useLanguageSwitch();
  const { state: installState, install } = useInstall();
  const { signOut, pending } = useSignOut();

  return (
    <Drawer.Root open={open} onOpenChange={setOpen}>
      <Drawer.Trigger asChild>{trigger}</Drawer.Trigger>
      <Drawer.Portal>
        <Drawer.Overlay className="fixed inset-0 z-50 bg-(--scrim)" />
        <Drawer.Content
          className="fixed inset-x-0 bottom-0 z-50 flex max-h-[92dvh] flex-col rounded-t-sheet border-t border-hairline bg-surface outline-none"
          aria-describedby={undefined}
        >
          <div className="mx-auto mt-2.5 mb-1 h-1 w-10 shrink-0 rounded-full bg-seam" aria-hidden="true" />
          <div className="scroll-thin overflow-y-auto px-4 pb-[calc(env(safe-area-inset-bottom)+16px)]">
            <div className="flex items-center gap-3 py-3">
              <PersonAvatar id={viewer.personId} name={viewer.displayName} size={44} />
              <div className="min-w-0 flex-1">
                <Drawer.Title className="truncate text-[1.0625rem] font-semibold text-ink">
                  {viewer.displayName}
                </Drawer.Title>
                <p className="text-meta text-ink-muted">{t(viewer.via === "code" ? "signedInCode" : "signedIn")}</p>
              </div>
            </div>

            <Link
              href={`/people/${viewer.personId}`}
              onClick={() => setOpen(false)}
              className="flex h-12 items-center gap-3 rounded-control px-1 text-[0.9375rem] font-medium text-ink active:bg-hoodie"
            >
              <UserRound className="size-5 text-ink-muted" aria-hidden="true" />
              {t("yourPage")}
            </Link>

            <SheetRow label={t("lighting")}>
              <Segmented
                label={t("lighting")}
                value={choice}
                onChange={setChoice}
                options={options.map((o) => ({ value: o.value, label: o.label }))}
              />
            </SheetRow>
            <SheetRow label={t("language")}>
              <Segmented
                label={t("language")}
                value={current}
                onChange={switchTo}
                options={LANGS.map((l) => ({ value: l.locale, label: <span lang={l.locale}>{l.name}</span> }))}
              />
            </SheetRow>

            {installState === "prompt" ? (
              <button
                type="button"
                onClick={() => void install()}
                className="flex h-12 w-full items-center gap-3 rounded-control px-1 text-left text-[0.9375rem] font-medium text-ink active:bg-hoodie"
              >
                <Download className="size-5 text-ink-muted" aria-hidden="true" />
                {t("install")}
              </button>
            ) : null}
            {installState === "ios" ? (
              <div className="flex gap-3 border-t border-hairline px-1 py-3.5 text-meta text-ink-soft">
                <Share className="mt-0.5 size-5 shrink-0 text-ink-muted" aria-hidden="true" />
                <p>{t("installIos")}</p>
              </div>
            ) : null}

            <div className="mt-1 border-t border-hairline pt-1" />
            <button
              type="button"
              disabled={pending}
              onClick={signOut}
              className="flex h-12 w-full items-center gap-3 rounded-control px-1 text-left text-[0.9375rem] font-medium text-danger active:bg-hoodie"
            >
              <LogOut className="size-5" aria-hidden="true" />
              {t("signOut")}
            </button>
          </div>
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
}

function SheetRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-hairline px-1 py-3">
      <span className="text-[0.9375rem] font-medium text-ink">{label}</span>
      {children}
    </div>
  );
}
