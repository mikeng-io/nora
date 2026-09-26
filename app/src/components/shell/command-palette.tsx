"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocale, useTranslations } from "next-intl";
import {
  BookOpenText,
  CalendarDays,
  CornerDownLeft,
  Gamepad2,
  Languages,
  LogOut,
  Moon,
  Search,
  Sun,
  Wrench,
} from "lucide-react";
import { useRouter } from "@/i18n/routing";
import { PersonAvatar } from "@/components/ui/avatar";
import { useDoctrine, usePlans, useRoster, useServers, useSkills, useTools } from "@/lib/queries";
import { useTheme } from "@/lib/theme";
import { formatWeekdayDate } from "@/lib/format";
import { paletteScore } from "@/lib/palette";
import { MAIN_NAV, NORA_NAV } from "./nav";
import { NavIcon } from "./nav-icon";
import { LANGS, useLanguageSwitch, useSignOut } from "./account";

// One search for the whole portal: pages, people, servers, plans, her
// doctrine and her tools. ⌘K or / opens it anywhere; "g" then a letter
// jumps straight to a page (g t today, g m map, g p people, g l plans,
// g g games, g n Nora).
const Ctx = createContext<{ open: () => void }>({ open: () => {} });
export const useCommandPalette = () => useContext(Ctx);

const JUMPS: Record<string, string> = {
  t: "/today",
  m: "/map",
  p: "/people",
  l: "/plans",
  g: "/games",
  n: "/nora",
};

function typingInField(e: KeyboardEvent) {
  const el = e.target as HTMLElement | null;
  return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
}

export function CommandPaletteProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const pendingG = useRef<number>(0);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
        return;
      }
      if (typingInField(e) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") {
        e.preventDefault();
        setOpen(true);
      } else if (e.key === "g") {
        pendingG.current = Date.now();
      } else if (Date.now() - pendingG.current < 1200 && JUMPS[e.key]) {
        pendingG.current = 0;
        router.push(JUMPS[e.key]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  const value = useMemo(() => ({ open: () => setOpen(true) }), []);
  return (
    <Ctx.Provider value={value}>
      {children}
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-(--scrim) animate-fade" />
          <Dialog.Content
            className="fixed inset-x-0 top-0 z-50 mx-auto flex h-dvh w-full flex-col bg-surface outline-none animate-fade sm:top-[12vh] sm:h-auto sm:max-h-[70vh] sm:max-w-xl sm:rounded-sheet sm:border sm:border-hairline sm:shadow-float"
            aria-describedby={undefined}
          >
            {open ? <Palette close={() => setOpen(false)} /> : null}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </Ctx.Provider>
  );
}

const itemClass =
  "flex min-h-11 cursor-pointer items-center gap-3 rounded-control px-3 py-2 text-[0.9375rem] text-ink outline-none select-none " +
  "data-[selected=true]:bg-hoodie [&>svg]:size-4 [&>svg]:shrink-0 [&>svg]:text-ink-muted";

const groupClass =
  "[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1.5 " +
  "[&_[cmdk-group-heading]]:text-label [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-ink-muted";

function Palette({ close }: { close: () => void }) {
  const t = useTranslations("search");
  const tNav = useTranslations("nav");
  const locale = useLocale();
  const router = useRouter();
  const roster = useRoster();
  const servers = useServers();
  const plans = usePlans();
  const doctrine = useDoctrine();
  const tools = useTools();
  const skills = useSkills();
  const { setChoice, resolved } = useTheme();
  const { current, switchTo } = useLanguageSwitch();
  const { signOut } = useSignOut();

  const go = useCallback(
    (href: string) => {
      close();
      router.push(href);
    },
    [close, router],
  );

  const otherLang = LANGS.find((l) => l.locale !== current)!;

  return (
    <Command
      label={t("label")}
      className="flex min-h-0 flex-1 flex-col"
      loop
      filter={paletteScore}
    >
      <Dialog.Title className="sr-only">{t("label")}</Dialog.Title>
      <div className="safe-top flex items-center gap-2 border-b border-hairline px-4">
        <Search className="size-5 shrink-0 text-ink-muted" aria-hidden="true" />
        <Command.Input
          autoFocus
          placeholder={t("placeholder")}
          className="h-14 min-w-0 flex-1 bg-transparent text-[1rem] text-ink outline-none placeholder:text-ink-muted"
        />
        <button
          type="button"
          onClick={close}
          className="h-9 rounded-control px-2 text-meta font-medium text-ink-muted hover:bg-hoodie sm:hidden"
        >
          {t("cancel")}
        </button>
        <kbd className="hidden rounded-md border border-hairline px-1.5 font-mono text-[0.6875rem] text-ink-muted sm:block">
          esc
        </kbd>
      </div>
      <Command.List className="scroll-thin min-h-0 flex-1 overflow-y-auto overscroll-contain p-2 sm:max-h-[56vh]">
        <Command.Empty className="px-3 py-10 text-center text-body text-ink-soft">
          {t("empty")}
        </Command.Empty>

        <Command.Group heading={t("pages")} className={groupClass}>
          {[...MAIN_NAV, ...NORA_NAV].map((it) => (
            <Command.Item
              key={it.key}
              value={`page ${tNav(it.key)}`}
              keywords={[it.key]}
              onSelect={() => go(it.href)}
              className={itemClass}
            >
              <span className="grid w-5 place-items-center text-ink-muted">
                <NavIcon item={it} active={false} size={17} />
              </span>
              {tNav(it.key)}
            </Command.Item>
          ))}
        </Command.Group>

        {roster.data?.length ? (
          <Command.Group heading={t("people")} className={groupClass}>
            {roster.data.map((p) => (
              <Command.Item
                key={p.personId}
                value={`person ${p.displayName}`}
                keywords={[p.personId]}
                onSelect={() => go(`/people/${p.personId}`)}
                className={itemClass}
              >
                <PersonAvatar id={p.personId} name={p.displayName} size={22} />
                {p.displayName}
              </Command.Item>
            ))}
          </Command.Group>
        ) : null}

        {servers.data?.length ? (
          <Command.Group heading={t("games")} className={groupClass}>
            {servers.data.map((s) => (
              <Command.Item
                key={s.id}
                value={`server ${s.displayName}`}
                keywords={[s.id]}
                onSelect={() => go(`/games/${s.id}`)}
                className={itemClass}
              >
                <Gamepad2 aria-hidden="true" />
                {s.displayName}
              </Command.Item>
            ))}
          </Command.Group>
        ) : null}

        {plans.data?.length ? (
          <Command.Group heading={t("plans")} className={groupClass}>
            {plans.data.map((p) => (
              <Command.Item
                key={p.id}
                value={`plan ${p.title} ${p.id}`}
                keywords={[p.venue.name ?? "", p.venue.text ?? ""]}
                onSelect={() => go(`/plans/${p.id}`)}
                className={itemClass}
              >
                <CalendarDays aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate">{p.title}</span>
                {p.when ? (
                  <span className="shrink-0 text-meta text-ink-muted">
                    {formatWeekdayDate(Date.parse(p.when), locale)}
                  </span>
                ) : null}
              </Command.Item>
            ))}
          </Command.Group>
        ) : null}

        {doctrine.data?.length ? (
          <Command.Group heading={t("doctrine")} className={groupClass}>
            {doctrine.data.map((d) => (
              <Command.Item
                key={d.name}
                value={`doctrine ${d.name} ${d.description}`}
                onSelect={() => go(`/nora/doctrine#${d.name}`)}
                className={itemClass}
              >
                <BookOpenText aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate">
                  <span className="capitalize">{d.name}</span>
                  <span className="text-ink-muted"> · {d.description}</span>
                </span>
              </Command.Item>
            ))}
          </Command.Group>
        ) : null}

        {tools.data?.length || skills.data?.length ? (
          <Command.Group heading={t("abilities")} className={groupClass}>
            {(tools.data ?? []).map((tool) => (
              <Command.Item
                key={`tool-${tool.name}`}
                value={`tool ${tool.name}`}
                keywords={[tool.description?.slice(0, 120) ?? ""]}
                onSelect={() => go(`/nora/abilities?q=${encodeURIComponent(tool.name)}`)}
                className={itemClass}
              >
                <Wrench aria-hidden="true" />
                <span className="font-mono text-meta">{tool.name}</span>
              </Command.Item>
            ))}
            {(skills.data ?? []).map((skill) => (
              <Command.Item
                key={`skill-${skill.name}`}
                value={`skill ${skill.name}`}
                keywords={[skill.description.slice(0, 120)]}
                onSelect={() => go(`/nora/abilities?tab=skills&q=${encodeURIComponent(skill.name)}`)}
                className={itemClass}
              >
                <BookOpenText aria-hidden="true" />
                <span className="font-mono text-meta">{skill.name}</span>
              </Command.Item>
            ))}
          </Command.Group>
        ) : null}

        <Command.Group heading={t("actions")} className={groupClass}>
          <Command.Item
            value={`action ${t("lighting")}`}
            onSelect={() => {
              setChoice(resolved === "dark" ? "light" : "dark");
              close();
            }}
            className={itemClass}
          >
            {resolved === "dark" ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
            {resolved === "dark" ? t("toStudio") : t("toNight")}
          </Command.Item>
          <Command.Item
            value={`action language ${otherLang.name}`}
            onSelect={() => {
              close();
              switchTo(otherLang.locale);
            }}
            className={itemClass}
          >
            <Languages aria-hidden="true" />
            <span lang={otherLang.locale}>{otherLang.name}</span>
          </Command.Item>
          <Command.Item value={`action ${t("signOut")}`} onSelect={signOut} className={itemClass}>
            <LogOut aria-hidden="true" />
            {t("signOut")}
          </Command.Item>
        </Command.Group>
      </Command.List>
      <div className="hidden items-center gap-4 border-t border-hairline px-4 py-2.5 text-label text-ink-muted sm:flex">
        <span className="flex items-center gap-1.5">
          <CornerDownLeft className="size-3.5" aria-hidden="true" /> {t("hintOpen")}
        </span>
        <span>↑↓ {t("hintMove")}</span>
        <span className="ml-auto">{t("hintJump")}</span>
      </div>
    </Command>
  );
}
